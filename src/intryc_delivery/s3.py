"""S3 publication with mandatory streamed readback and conditional readiness."""

import hashlib
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import boto3
from boto3.exceptions import Boto3Error
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)

from intryc_delivery.config import Settings
from intryc_delivery.errors import ExitCode, fail
from intryc_delivery.filesystem import CHUNK_BYTES, regular_file
from intryc_delivery.validation import Delivery, LocalObject, schema_validators

AWS_CONFIG = Config(
    connect_timeout=10, read_timeout=60, retries={"mode": "standard", "total_max_attempts": 5}
)
MISSING = {"404", "NoSuchKey", "NotFound"}


@contextmanager
def aws_errors():
    try:
        yield
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        messages = {
            "AccessDenied": "Grant prefix-scoped ListBucket, GetObject and PutObject access; allow KMS use if applicable. Readback is mandatory.",
            "403": "Grant prefix-scoped ListBucket, GetObject and PutObject access. Readback is mandatory.",
            "NoSuchBucket": "Check the configured bucket and AWS account.",
            "PermanentRedirect": "Set --region to the bucket's AWS Region.",
            "AuthorizationHeaderMalformed": "Set --region to the bucket's AWS Region.",
            "KMSAccessDeniedException": "Allow decrypt and data-key operations on the bucket's KMS key.",
            "SlowDown": "S3 throttling persisted after bounded retries; retry later.",
            "ExpiredToken": "Refresh your AWS credentials and retry.",
        }
        fail(
            "AWS_FAILURE",
            messages.get(
                code,
                "AWS rejected the operation. Check permissions, Region and credentials, then retry.",
            ),
            exit_code=ExitCode.TRANSPORT,
        )
    except (BotoCoreError, Boto3Error):
        fail(
            "TRANSPORT_FAILURE",
            "AWS communication or credentials failed after bounded retries.",
            exit_code=ExitCode.TRANSPORT,
        )


class S3Publisher:
    def __init__(self, settings: Settings, *, client=None, session=None, progress=None):
        if not settings.bucket or not settings.prefix:
            fail("MISSING_TARGET", "Supply a bucket and prefix.", exit_code=ExitCode.CONFIGURATION)
        self.settings = settings
        self.bucket = settings.bucket
        self.prefix = settings.prefix
        self.session = session
        self.client = client
        self.progress = progress or (lambda _: None)
        if self.client is None:
            with aws_errors():
                self.session = self.session or boto3.Session(
                    profile_name=settings.profile, region_name=settings.region
                )
                self.client = self.session.client(
                    "s3", config=AWS_CONFIG, endpoint_url=settings.endpoint_url
                )

    def _head(self, key):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in MISSING:
                return None
            raise

    def _root(self, delivery: Delivery) -> str:
        root = f"{self.prefix}/deliveries/{delivery.delivery_id}/"
        keys = [root + o.path for o in delivery.objects] + [self._marker(delivery)]
        if any(len(k.encode("utf-8")) > 1024 for k in keys):
            fail(
                "KEY_TOO_LONG",
                "Combined prefix, delivery ID and relative path exceed the S3 key limit.",
                exit_code=ExitCode.CONFIGURATION,
            )
        return root

    def _marker(self, delivery):
        return f"{self.prefix}/_ready/{delivery.delivery_id}"

    def _published(self, delivery):
        marker = self._head(self._marker(delivery))
        if marker is not None and marker.get("ContentLength") != 0:
            fail(
                "INVALID_READY_MARKER",
                "The existing ready marker must be empty.",
                exit_code=ExitCode.TRANSPORT,
            )
        return marker is not None

    def _inventory(self, root):
        keys = set()
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=root
        ):
            for obj in page.get("Contents", []):
                if not obj["Key"].startswith(root):
                    fail(
                        "INVALID_LISTING",
                        "S3 returned an object outside the requested prefix.",
                        exit_code=ExitCode.TRANSPORT,
                    )
                keys.add(obj["Key"])
                if len(keys) > 60_001:
                    fail(
                        "REMOTE_INVENTORY",
                        "Remote delivery exceeds the maximum inventory size.",
                        exit_code=ExitCode.TRANSPORT,
                    )
        return keys

    def _matches(self, key: str, item: LocalObject) -> bool:
        head = self._head(key)
        if (
            head is None
            or head.get("ContentLength") != item.bytes
            or head.get("ContentType") != item.content_type
        ):
            return False
        # Always hash real remote bytes. Neither ETag nor custom metadata proves
        # the whole-file digest for multipart uploads.
        for attempt in range(3):
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key, IfMatch=head["ETag"])
                body = response["Body"]
                digest, size = hashlib.sha256(), 0
                try:
                    if (
                        response.get("ContentLength") != item.bytes
                        or response.get("ContentType") != item.content_type
                    ):
                        return False
                    while chunk := body.read(CHUNK_BYTES):
                        size += len(chunk)
                        if size > item.bytes:
                            return False
                        digest.update(chunk)
                finally:
                    body.close()
                return size == item.bytes and digest.hexdigest() == item.sha256
            except (
                ConnectionClosedError,
                ConnectTimeoutError,
                EndpointConnectionError,
                IncompleteReadError,
                ReadTimeoutError,
                ResponseStreamingError,
            ):
                if attempt == 2:
                    raise
                time.sleep(0.25 * 2**attempt)
        return False

    def _verify(self, delivery: Delivery):
        self.progress(f"Verifying remote inventory and {len(delivery.objects)} files.")
        root = self._root(delivery)
        if self._inventory(root) != {root + obj.path for obj in delivery.objects}:
            fail(
                "REMOTE_INVENTORY_MISMATCH",
                "Remote inventory must exactly match the local delivery.",
                exit_code=ExitCode.TRANSPORT,
            )

        def verify_one(item):
            if not self._matches(root + item.path, item):
                fail(
                    "REMOTE_CONTENT_MISMATCH",
                    "Remote bytes or Content-Type differ from the validated delivery.",
                    path=item.path,
                    exit_code=ExitCode.TRANSPORT,
                )

        with ThreadPoolExecutor(max_workers=self.settings.concurrency) as pool:
            list(pool.map(verify_one, delivery.objects))
        self.progress("Remote inventory and file checksums verified.")

    def _create_object(self, delivery: Delivery, item: LocalObject, key: str) -> None:
        part_bytes = 8 * 1024 * 1024
        metadata = {
            "sha256": item.sha256,
            "delivery-id-base64": b64encode(delivery.delivery_id.encode("utf-8")).decode("ascii"),
        }
        with regular_file(delivery.root / item.path) as stream:
            if item.bytes < part_bytes:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=stream,
                    ContentType=item.content_type,
                    Metadata=metadata,
                    IfNoneMatch="*",
                )
                return
            upload_id = self.client.create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                ContentType=item.content_type,
                Metadata=metadata,
            )["UploadId"]
            try:
                parts = []
                while chunk := stream.read(part_bytes):
                    number = len(parts) + 1
                    result = self.client.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=number,
                        Body=chunk,
                    )
                    parts.append({"PartNumber": number, "ETag": result["ETag"]})
                self.client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                    IfNoneMatch="*",
                )
            except BaseException:
                try:
                    self.client.abort_multipart_upload(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload_id,
                    )
                except (BotoCoreError, ClientError):
                    self.progress(
                        "Multipart cleanup failed; bucket lifecycle cleanup may be needed."
                    )
                raise

    def upload(self, delivery: Delivery) -> bool:
        self.progress(f"Uploading or resuming {len(delivery.objects)} files.")
        with aws_errors():
            root = self._root(delivery)
            if self._published(delivery):
                self._verify(delivery)
                return True
            unexpected = self._inventory(root) - {root + obj.path for obj in delivery.objects}
            if unexpected:
                fail(
                    "UNEXPECTED_REMOTE_OBJECT",
                    "Remote delivery contains unlisted objects. Use a fresh delivery ID or correct the unpublished inventory.",
                    exit_code=ExitCode.TRANSPORT,
                )

            def upload_one(item):
                key = root + item.path
                if self._matches(key, item):
                    return
                if self._published(delivery):
                    fail(
                        "DELIVERY_ALREADY_PUBLISHED",
                        "A ready marker appeared while uploading; stop all other writers.",
                        exit_code=ExitCode.TRANSPORT,
                    )
                try:
                    self._create_object(delivery, item, key)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") not in {
                        "412",
                        "PreconditionFailed",
                    }:
                        raise
                    # Another writer (or a retried successful request) won the
                    # conditional write. Only identical contents are replay-safe.
                    if not self._matches(key, item):
                        fail(
                            "REMOTE_OBJECT_CONFLICT",
                            "An existing object differs. Use a fresh delivery ID; existing objects are never overwritten.",
                            path=item.path,
                            exit_code=ExitCode.TRANSPORT,
                        )

            with ThreadPoolExecutor(max_workers=self.settings.concurrency) as pool:
                list(
                    pool.map(upload_one, (o for o in delivery.objects if o.path != "manifest.json"))
                )
            # Complete data transfers before uploading the manifest, matching the
            # manual delivery contract. Readiness remains a separate final step.
            for item in delivery.objects:
                if item.path == "manifest.json":
                    upload_one(item)
            self._verify(delivery)
            return False

    def publish(self, delivery: Delivery) -> bool:
        with aws_errors():
            published = self._published(delivery)
            self._verify(delivery)
            if published:
                return True
            try:
                self.client.put_object(
                    Bucket=self.bucket, Key=self._marker(delivery), Body=b"", IfNoneMatch="*"
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                    raise
                if not self._published(delivery):
                    raise
                self._verify(delivery)
                return True
            return False

    def doctor(self, *, probe: bool = False):
        schema_validators()
        with aws_errors():
            self.session.client("sts", config=AWS_CONFIG).get_caller_identity()
            location = self.client.get_bucket_location(Bucket=self.bucket).get("LocationConstraint")
            actual_region = (
                "us-east-1" if location is None else "eu-west-1" if location == "EU" else location
            )
            if self.settings.region and self.settings.region != actual_region:
                fail(
                    "REGION_MISMATCH",
                    "Configure the AWS Region containing the bucket.",
                    exit_code=ExitCode.CONFIGURATION,
                )
            self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix + "/", MaxKeys=1)
            if probe:
                key = f"{self.prefix}/deliveries/doctor-{uuid4()}/probe.txt"
                content = b"Intryc delivery CLI access probe. No ready marker is created.\n"
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=content,
                    ContentType="text/plain",
                    IfNoneMatch="*",
                )
                item = LocalObject(
                    path="probe.txt",
                    bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    content_type="text/plain",
                )
                if not self._matches(key, item):
                    fail(
                        "PROBE_FAILED",
                        "Probe bytes could not be verified.",
                        exit_code=ExitCode.TRANSPORT,
                    )
