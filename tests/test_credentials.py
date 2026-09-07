"""Exercise the real SDK assume-role provider without contacting AWS."""

from datetime import datetime, timedelta, timezone

import botocore.session
from botocore.stub import ANY, Stubber

from intryc_delivery.config import Settings
from intryc_delivery.s3 import S3Publisher


def test_role_profile_refreshes_for_the_existing_s3_client(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(
        "[profile delivery]\nrole_arn = arn:aws:iam::123456789012:role/delivery\n"
        "source_profile = source\nregion = us-east-1\n"
    )
    credentials_file = tmp_path / "credentials"
    credentials_file.write_text(
        "[source]\naws_access_key_id = synthetic-source-key\n"
        "aws_secret_access_key = synthetic-source-secret\n"
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    session = botocore.session.get_session()
    sts = session.create_client(
        "sts",
        region_name="us-east-1",
        aws_access_key_id="synthetic-key",
        aws_secret_access_key="synthetic-secret",
    )
    stub = Stubber(sts)
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    for token in ("synthetic-first-token", "synthetic-refreshed-token"):
        stub.add_response(
            "assume_role",
            {
                "Credentials": {
                    "AccessKeyId": "ASIASYNTHETICEXAMPLE1",
                    "SecretAccessKey": "synthetic-secret-for-sdk-test-only",
                    "SessionToken": token,
                    "Expiration": expiry,
                }
            },
            {"RoleArn": "arn:aws:iam::123456789012:role/delivery", "RoleSessionName": ANY},
        )
    original = botocore.session.Session.create_client

    def create_client(self, service_name, *args, **kwargs):
        return sts if service_name == "sts" else original(self, service_name, *args, **kwargs)

    monkeypatch.setattr(botocore.session.Session, "create_client", create_client)
    with stub:
        target = S3Publisher(
            Settings(bucket="example-bucket", prefix="incoming", profile="delivery")
        )
        credentials = target.session.get_credentials()
        first = target.client.generate_presigned_url(
            "get_object", Params={"Bucket": "example-bucket", "Key": "incoming/example"}
        )
        assert "synthetic-first-token" in first
        # Expire both the SDK's live credentials and its assume-role response cache.
        credentials._expiry_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        for cached in (
            target.session._session.get_component("credential_provider")
            .get_provider("assume-role")
            .cache.values()
        ):
            cached["Credentials"]["Expiration"] = credentials._expiry_time
        second = target.client.generate_presigned_url(
            "get_object", Params={"Bucket": "example-bucket", "Key": "incoming/example"}
        )
        assert "synthetic-refreshed-token" in second
        stub.assert_no_pending_responses()
