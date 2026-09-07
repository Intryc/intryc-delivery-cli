import copy
import hashlib
import json
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "delivery"
    shutil.copytree(Path(__file__).parents[1] / "examples" / "delivery", path)
    return path


def save_ticket(root, name, payload):
    path = root / "ticket_details" / name
    content = (json.dumps(payload, indent=2) + "\n").encode()
    path.write_bytes(content)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for ticket in manifest["tickets"]:
        if ticket["path"] == f"ticket_details/{name}":
            ticket.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest_path.write_text(json.dumps(manifest))


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.events = []
        self.deny_reads = False
        self.corrupt_upload = False
        self.fail_upload = False
        self.marker_race = False

    def head_object(self, *, Bucket, Key):
        self.events.append(("head", Key))
        if self.deny_reads:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "secret"}}, "HeadObject"
            )
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        obj = self.objects[Key]
        return {
            "ContentLength": len(obj["Body"]),
            "ContentType": obj["ContentType"],
            "ETag": hashlib.md5(obj["Body"]).hexdigest(),
        }

    def get_object(self, *, Bucket, Key, IfMatch):
        self.events.append(("get", Key))
        head = self.head_object(Bucket=Bucket, Key=Key)
        if head["ETag"] != IfMatch:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        return {**head, "Body": BytesIO(self.objects[Key]["Body"])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        self.events.append(("list", Prefix))
        # Small pages deliberately exercise inventory aggregation.
        for key in sorted(self.objects):
            if key.startswith(Prefix):
                yield {"Contents": [{"Key": key}]}

    def upload_fileobj(self, stream, bucket, key, *, ExtraArgs, Config):
        self.events.append(("upload", key))
        assert Config.multipart_threshold == 8 * 1024 * 1024
        assert Config.max_concurrency == 1
        if self.fail_upload:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        content = stream.read()
        if self.corrupt_upload:
            content = b"x" * len(content)
        self.objects[key] = {"Body": content, **copy.deepcopy(ExtraArgs)}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ContentType="application/octet-stream"):
        assert IfNoneMatch == "*"
        self.events.append(("put", Key))
        if Key in self.objects or self.marker_race:
            self.objects.setdefault(Key, {"Body": b"", "ContentType": ContentType})
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = {"Body": Body, "ContentType": ContentType}


@pytest.fixture
def s3():
    return FakeS3()
