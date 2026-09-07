import hashlib
from base64 import b64decode
from datetime import UTC, datetime
from io import BytesIO

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from intryc_delivery.config import Settings
from intryc_delivery.errors import DeliveryError, ExitCode
from intryc_delivery.s3 import S3Publisher
from intryc_delivery.validation import Delivery, LocalObject


@pytest.mark.parametrize("fail_part", [False, True])
@pytest.mark.parametrize("delivery_id", ["multipart", "παράδοση"])
def test_conditional_multipart_upload_reads_back(tmp_path, fail_part, delivery_id):
    # Actual SDK operations, including the atomic multipart completion condition.
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    content = b"x" * (9 * 1024 * 1024)
    (tmp_path / "media.wav").write_bytes(content)
    item = LocalObject(
        path="media.wav",
        bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="audio/wav",
    )
    delivery = Delivery(root=tmp_path, delivery_id=delivery_id, manifest={}, objects=[item])
    key = f"incoming/deliveries/{delivery_id}/media.wav"

    def check_metadata(params, **kwargs):
        encoded = params["Metadata"]["delivery-id-base64"]
        assert b64decode(encoded).decode("utf-8") == delivery_id
        encoded.encode("ascii")

    client.meta.events.register("before-parameter-build.s3.CreateMultipartUpload", check_metadata)
    metadata = {
        "ContentLength": len(content),
        "ContentType": "audio/wav",
        "ETag": '"multipart-etag"',
    }
    with Stubber(client) as stub:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_response("list_objects_v2", {"IsTruncated": False, "Contents": []})
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_response("create_multipart_upload", {"UploadId": "upload-1"})
        if fail_part:
            stub.add_client_error(
                "upload_part", service_error_code="AccessDenied", http_status_code=403
            )
            stub.add_response("abort_multipart_upload", {})
            publisher = S3Publisher(
                Settings(bucket="example-bucket", prefix="incoming"), client=client
            )
            with pytest.raises(DeliveryError) as exc:
                publisher.upload(delivery)
            assert exc.value.exit_code == ExitCode.TRANSPORT
            stub.assert_no_pending_responses()
            return
        stub.add_response("upload_part", {"ETag": '"part-1"'})
        stub.add_response("upload_part", {"ETag": '"part-2"'})
        stub.add_response(
            "complete_multipart_upload",
            {"ETag": '"multipart-etag"'},
            {
                "Bucket": "example-bucket",
                "Key": key,
                "UploadId": "upload-1",
                "MultipartUpload": ANY,
                "IfNoneMatch": "*",
            },
        )
        stub.add_response(
            "list_objects_v2",
            {
                "IsTruncated": False,
                "Contents": [{"Key": key, "LastModified": datetime.now(UTC), "Size": len(content)}],
            },
        )
        stub.add_response("head_object", metadata)
        stub.add_response(
            "get_object", {**metadata, "Body": StreamingBody(BytesIO(content), len(content))}
        )
        publisher = S3Publisher(Settings(bucket="example-bucket", prefix="incoming"), client=client)
        assert publisher.upload(delivery) is False
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict"])
def test_failed_conditional_multipart_completion_aborts_without_overwriting(tmp_path, code):
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    content = b"x" * (8 * 1024 * 1024)
    (tmp_path / "media.wav").write_bytes(content)
    item = LocalObject(
        path="media.wav",
        bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="audio/wav",
    )
    delivery = Delivery(root=tmp_path, delivery_id="race", manifest={}, objects=[item])
    target = S3Publisher(Settings(bucket="example-bucket", prefix="incoming"), client=client)
    key = "incoming/deliveries/race/media.wav"
    with Stubber(client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": "upload-1"})
        stub.add_response("upload_part", {"ETag": '"part-1"'})
        stub.add_client_error(
            "complete_multipart_upload",
            service_error_code=code,
            http_status_code=412 if code == "PreconditionFailed" else 409,
            expected_params={
                "Bucket": "example-bucket",
                "Key": key,
                "UploadId": "upload-1",
                "MultipartUpload": {"Parts": [{"PartNumber": 1, "ETag": '"part-1"'}]},
                "IfNoneMatch": "*",
            },
        )
        stub.add_response(
            "abort_multipart_upload",
            {},
            {
                "Bucket": "example-bucket",
                "Key": key,
                "UploadId": "upload-1",
            },
        )
        with pytest.raises(ClientError) as exc:
            target._create_object(delivery, item, key)
        assert exc.value.response["Error"]["Code"] == code
        stub.assert_no_pending_responses()
