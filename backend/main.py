from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os

# Database helpers
from database import db, create_document, get_documents

# Boto3 for AWS integration
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

app = FastAPI(title="AWS Cleanup Tool API", version="1.0.0")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ListRequest(BaseModel):
    filter_scope: str  # "account" | "org" | "tag"
    account_ids: Optional[List[str]] = None
    tag_key: Optional[str] = None
    tag_value: Optional[str] = None
    resource_types: Optional[List[str]] = None
    regions: Optional[List[str]] = None


class DeleteRequest(BaseModel):
    resources: List[Dict[str, Any]]  # list of objects with resource_type, id, region, account_id


def _aws_clients(service: str, region: str):
    session = boto3.Session()
    return session.client(service, region_name=region, config=Config(retries={"max_attempts": 3}))


def _default_regions() -> List[str]:
    # Common commercial regions; users can override in request
    return [
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "eu-west-1", "eu-west-2", "eu-central-1",
        "ap-south-1", "ap-southeast-1", "ap-southeast-2",
    ]


@app.get("/")
async def root():
    return {"status": "ok", "name": "AWS Cleanup Tool API"}


@app.get("/test")
async def test():
    # Simple DB connectivity check (if configured)
    try:
        if db is None:
            return {"database": "not-configured"}
        db.command("ping")
        return {"database": "ok"}
    except Exception as e:
        return {"database": f"error: {e}"}


@app.post("/list")
async def list_resources(payload: ListRequest):
    scope = payload.filter_scope
    regions = payload.regions or _default_regions()
    resource_types = payload.resource_types or ["ec2:instance", "s3:bucket", "rds:db", "iam:user"]

    matched: List[Dict[str, Any]] = []

    try:
        for region in regions:
            for rtype in resource_types:
                if rtype == "ec2:instance":
                    ec2 = _aws_clients("ec2", region)
                    resp = ec2.describe_instances()
                    for resv in resp.get("Reservations", []):
                        for inst in resv.get("Instances", []):
                            tags = {t["Key"]: t.get("Value", "") for t in inst.get("Tags", [])}
                            account_id = boto3.client("sts").get_caller_identity()["Account"]
                            if scope == "tag":
                                if (payload.tag_key and payload.tag_key in tags and
                                   (payload.tag_value is None or tags.get(payload.tag_key) == payload.tag_value)):
                                    matched.append({
                                        "id": inst.get("InstanceId"),
                                        "resource_type": rtype,
                                        "region": region,
                                        "account_id": account_id,
                                        "tags": tags,
                                        "state": inst.get("State", {}).get("Name"),
                                    })
                            else:
                                matched.append({
                                    "id": inst.get("InstanceId"),
                                    "resource_type": rtype,
                                    "region": region,
                                    "account_id": account_id,
                                    "tags": tags,
                                    "state": inst.get("State", {}).get("Name"),
                                })
                elif rtype == "s3:bucket":
                    s3 = _aws_clients("s3", region)
                    # S3 is global, but we try to fetch list then get tags per bucket
                    s3g = boto3.client("s3")
                    buckets = s3g.list_buckets().get("Buckets", [])
                    for b in buckets:
                        name = b.get("Name")
                        tags = {}
                        try:
                            tagset = s3g.get_bucket_tagging(Bucket=name).get("TagSet", [])
                            tags = {t["Key"]: t.get("Value", "") for t in tagset}
                        except ClientError:
                            pass
                        account_id = boto3.client("sts").get_caller_identity()["Account"]
                        if scope == "tag":
                            if (payload.tag_key and payload.tag_key in tags and
                               (payload.tag_value is None or tags.get(payload.tag_key) == payload.tag_value)):
                                matched.append({
                                    "id": name,
                                    "resource_type": rtype,
                                    "region": region,
                                    "account_id": account_id,
                                    "tags": tags,
                                })
                        else:
                            matched.append({
                                "id": name,
                                "resource_type": rtype,
                                "region": region,
                                "account_id": account_id,
                                "tags": tags,
                            })
                elif rtype == "rds:db":
                    rds = _aws_clients("rds", region)
                    dbs = rds.describe_db_instances().get("DBInstances", [])
                    for dbi in dbs:
                        arn = dbi.get("DBInstanceArn")
                        tags_list = rds.list_tags_for_resource(ResourceName=arn).get("TagList", [])
                        tags = {t["Key"]: t.get("Value", "") for t in tags_list}
                        account_id = boto3.client("sts").get_caller_identity()["Account"]
                        if scope == "tag":
                            if (payload.tag_key and payload.tag_key in tags and
                               (payload.tag_value is None or tags.get(payload.tag_key) == payload.tag_value)):
                                matched.append({
                                    "id": dbi.get("DBInstanceIdentifier"),
                                    "resource_type": rtype,
                                    "region": region,
                                    "account_id": account_id,
                                    "tags": tags,
                                    "state": dbi.get("DBInstanceStatus"),
                                })
                        else:
                            matched.append({
                                "id": dbi.get("DBInstanceIdentifier"),
                                "resource_type": rtype,
                                "region": region,
                                "account_id": account_id,
                                "tags": tags,
                                "state": dbi.get("DBInstanceStatus"),
                            })
                elif rtype == "iam:user":
                    iam = boto3.client("iam")
                    users = iam.list_users().get("Users", [])
                    for u in users:
                        arn = u.get("Arn")
                        tags = {}
                        try:
                            resp = iam.list_user_tags(UserName=u.get("UserName"))
                            tags = {t["Key"]: t.get("Value", "") for t in resp.get("Tags", [])}
                        except ClientError:
                            pass
                        account_id = boto3.client("sts").get_caller_identity()["Account"]
                        if scope == "tag":
                            if (payload.tag_key and payload.tag_key in tags and
                               (payload.tag_value is None or tags.get(payload.tag_key) == payload.tag_value)):
                                matched.append({
                                    "id": u.get("UserName"),
                                    "resource_type": rtype,
                                    "region": region,
                                    "account_id": account_id,
                                    "tags": tags,
                                })
                        else:
                            matched.append({
                                "id": u.get("UserName"),
                                "resource_type": rtype,
                                "region": region,
                                "account_id": account_id,
                                "tags": tags,
                            })
                else:
                    # Unsupported resource type; skip
                    continue
    except ClientError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"count": len(matched), "resources": matched}


@app.post("/delete")
async def delete_resources(payload: DeleteRequest):
    results = []
    errors = []
    try:
        for r in payload.resources:
            rtype = r.get("resource_type")
            region = r.get("region")
            rid = r.get("id")
            if rtype == "ec2:instance":
                ec2 = _aws_clients("ec2", region)
                try:
                    ec2.terminate_instances(InstanceIds=[rid])
                    results.append({"id": rid, "status": "terminated"})
                except ClientError as e:
                    errors.append({"id": rid, "error": str(e)})
            elif rtype == "s3:bucket":
                s3 = boto3.resource("s3")
                bucket = s3.Bucket(rid)
                try:
                    # Must empty bucket before delete
                    bucket.objects.all().delete()
                    bucket.object_versions.delete()
                    bucket.delete()
                    results.append({"id": rid, "status": "deleted"})
                except ClientError as e:
                    errors.append({"id": rid, "error": str(e)})
            elif rtype == "rds:db":
                rds = _aws_clients("rds", region)
                try:
                    rds.delete_db_instance(DBInstanceIdentifier=rid, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
                    results.append({"id": rid, "status": "deletion-started"})
                except ClientError as e:
                    errors.append({"id": rid, "error": str(e)})
            elif rtype == "iam:user":
                iam = boto3.client("iam")
                try:
                    # Detach policies and delete access keys before user deletion
                    for k in iam.list_access_keys(UserName=rid).get("AccessKeyMetadata", []):
                        iam.delete_access_key(UserName=rid, AccessKeyId=k.get("AccessKeyId"))
                    for p in iam.list_attached_user_policies(UserName=rid).get("AttachedPolicies", []):
                        iam.detach_user_policy(UserName=rid, PolicyArn=p.get("PolicyArn"))
                    for g in iam.list_groups_for_user(UserName=rid).get("Groups", []):
                        iam.remove_user_from_group(UserName=rid, GroupName=g.get("GroupName"))
                    iam.delete_user(UserName=rid)
                    results.append({"id": rid, "status": "deleted"})
                except ClientError as e:
                    errors.append({"id": rid, "error": str(e)})
            else:
                errors.append({"id": rid, "error": "unsupported resource type"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"deleted": results, "errors": errors}
