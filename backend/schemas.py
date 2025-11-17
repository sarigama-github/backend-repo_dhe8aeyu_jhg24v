"""
Database Schemas for AWS Cleanup Tool

Each Pydantic model represents a collection in MongoDB. The collection name is the lowercase
of the class name by convention of the provided database helpers.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

class CleanupRule(BaseModel):
    """
    cleanup rule schema
    collection name: "cleanuprule"
    """
    name: str = Field(..., description="Human-friendly rule name")
    scope: Literal["account", "org", "tag"] = Field(
        ..., description="What to target: a whole account, the organization, or by tag"
    )
    # For scope=="account": optional role or account id list
    account_ids: Optional[List[str]] = Field(
        default=None, description="Specific AWS account IDs to target"
    )
    tag_key: Optional[str] = Field(default=None, description="Tag key to filter")
    tag_value: Optional[str] = Field(default=None, description="Tag value to match (e.g., email)")
    resource_types: Optional[List[str]] = Field(
        default=None, description="AWS resource type identifiers (e.g., ec2:instance, s3:bucket)"
    )
    dry_run: bool = Field(True, description="If true, do not delete, only list")
    created_by: Optional[str] = Field(default=None, description="User who created the rule")

class ScanRecord(BaseModel):
    """
    scanrecord schema
    collection name: "scanrecord"
    """
    rule_name: str
    status: Literal["running", "completed", "failed"] = "running"
    matched_count: int = 0
    deleted_count: int = 0
    details: Dict[str, Any] = {}

class AwsResource(BaseModel):
    """
    awsresource schema to keep history of discovered resources
    collection name: "awsresource"
    """
    resource_id: str
    resource_type: str
    region: str
    account_id: str
    tags: Dict[str, str] = {}
    state: Optional[str] = None
    found_in_scan: Optional[str] = None  # scanrecord id or rule name
