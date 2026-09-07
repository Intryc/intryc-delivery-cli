import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from intryc_delivery.config import Settings
from intryc_delivery.errors import DeliveryError, ExitCode
from intryc_delivery.s3 import AWS_CONFIG, S3Publisher
from intryc_delivery.validation import validate


def publisher(s3):
    return S3Publisher(
        Settings(bucket="example-bucket", prefix="incoming", concurrency=2), client=s3
    )


def test_upload_publish_and_replay(root, s3):
    delivery = validate(root)
    target = publisher(s3)
    assert not target.upload(delivery)
    assert not any("/_ready/" in key for key in s3.objects)
    uploaded = sum(event == "upload" for event, _ in s3.events)
    assert uploaded == 6
    uploads = [key for event, key in s3.events if event == "upload"]
    assert uploads[-1].endswith("/manifest.json")
    assert not target.publish(delivery)
    assert s3.events[-1] == ("put", "incoming/_ready/example-delivery-1")
    assert s3.objects[s3.events[-1][1]]["Body"] == b""
    assert target.upload(delivery)
    assert target.publish(delivery)
    assert sum(event == "upload" for event, _ in s3.events) == uploaded


def test_resume_missing_object(root, s3):
    delivery = validate(root)
    target = publisher(s3)
    target.upload(delivery)
    missing = next(iter(s3.objects))
    del s3.objects[missing]
    s3.events.clear()
    target.upload(delivery)
    assert [key for event, key in s3.events if event == "upload"] == [missing]


@pytest.mark.parametrize("failure", ["corrupt", "denied", "upload", "extra", "missing"])
def test_never_publishes_on_failure(root, s3, failure):
    delivery = validate(root)
    target = publisher(s3)
    if failure == "corrupt":
        s3.corrupt_upload = True
    if failure == "denied":
        s3.deny_reads = True
    if failure == "upload":
        s3.fail_upload = True
    if failure == "extra":
        s3.objects["incoming/deliveries/example-delivery-1/unlisted"] = {
            "Body": b"",
            "ContentType": "text/plain",
        }
    with pytest.raises(DeliveryError) as exc:
        if failure != "missing":
            target.upload(delivery)
        target.publish(delivery)
    assert exc.value.exit_code == ExitCode.TRANSPORT
    assert not any("/_ready/" in key for key in s3.objects)
    assert "secret" not in str(exc.value.issues)


def test_published_corruption_cannot_be_overwritten(root, s3):
    delivery = validate(root)
    target = publisher(s3)
    target.upload(delivery)
    target.publish(delivery)
    key = "incoming/deliveries/example-delivery-1/assets/attachments/example.txt"
    original = s3.objects[key]["Body"]
    s3.objects[key]["Body"] = b"x" * len(original)
    s3.events.clear()
    with pytest.raises(DeliveryError):
        target.upload(delivery)
    assert not any(event == "upload" for event, _ in s3.events)


def test_conditional_publication_race_verifies_winner(root, s3):
    delivery = validate(root)
    target = publisher(s3)
    target.upload(delivery)
    s3.marker_race = True
    assert target.publish(delivery)


def test_nonempty_marker_rejected(root, s3):
    delivery = validate(root)
    s3.objects["incoming/_ready/example-delivery-1"] = {"Body": b"no", "ContentType": "text/plain"}
    with pytest.raises(DeliveryError):
        publisher(s3).upload(delivery)


def test_interrupted_read_is_retried_and_exhaustion_fails(root, s3, monkeypatch):
    target = publisher(s3)
    delivery = validate(root)
    target.upload(delivery)
    monkeypatch.setattr("intryc_delivery.s3.time.sleep", lambda _: None)
    original = s3.get_object
    attempts = 0

    def flaky(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise ReadTimeoutError(endpoint_url="https://example.test")
        return original(**kwargs)

    monkeypatch.setattr(s3, "get_object", flaky)
    assert target._matches(target._root(delivery) + delivery.objects[0].path, delivery.objects[0])
    assert attempts == 3

    def broken(**kwargs):
        raise ReadTimeoutError(endpoint_url="https://example.test")

    monkeypatch.setattr(s3, "get_object", broken)
    with pytest.raises(DeliveryError):
        target.publish(delivery)
    assert not any("/_ready/" in key for key in s3.objects)
    assert AWS_CONFIG.retries["total_max_attempts"] == 5


def test_credential_failure_is_not_retried_as_a_stream_failure(root, s3, monkeypatch):
    target = publisher(s3)
    delivery = validate(root)
    target.upload(delivery)
    attempts = 0

    def missing_credentials(**kwargs):
        nonlocal attempts
        attempts += 1
        raise NoCredentialsError()

    monkeypatch.setattr(s3, "get_object", missing_credentials)
    monkeypatch.setattr("intryc_delivery.s3.time.sleep", lambda _: None)
    with pytest.raises(NoCredentialsError):
        target._matches(target._root(delivery) + delivery.objects[0].path, delivery.objects[0])
    assert attempts == 1


@pytest.mark.parametrize("code", ["PermanentRedirect", "KMSAccessDeniedException", "SlowDown"])
def test_aws_error_classification(root, s3, monkeypatch, code):
    def broken(**kwargs):
        raise ClientError({"Error": {"Code": code, "Message": "private-payload"}}, "HeadObject")

    monkeypatch.setattr(s3, "head_object", broken)
    with pytest.raises(DeliveryError) as exc:
        publisher(s3).publish(validate(root))
    assert exc.value.exit_code == ExitCode.TRANSPORT
    assert "private-payload" not in str(exc.value.issues)


def test_doctor_checks_identity_region_listing_and_optional_probe(s3, monkeypatch):
    from unittest.mock import MagicMock

    session = MagicMock()
    s3.get_bucket_location = MagicMock(return_value={"LocationConstraint": None})
    s3.list_objects_v2 = MagicMock(return_value={})
    target = S3Publisher(
        Settings(bucket="example-bucket", prefix="incoming", region="us-east-1"),
        client=s3,
        session=session,
    )
    target.doctor()
    session.client.return_value.get_caller_identity.assert_called_once()
    s3.list_objects_v2.assert_called_once_with(
        Bucket="example-bucket", Prefix="incoming/", MaxKeys=1
    )
    assert not s3.objects
    target.doctor(probe=True)
    assert len(s3.objects) == 1
    assert all(key.startswith("incoming/deliveries/doctor-") for key in s3.objects)
    assert not any("/_ready/" in key for key in s3.objects)
    s3.get_bucket_location.return_value = {"LocationConstraint": "eu-west-1"}
    with pytest.raises(DeliveryError) as exc:
        target.doctor()
    assert exc.value.issues[0].code == "REGION_MISMATCH"
