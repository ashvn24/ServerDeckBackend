import datetime
import uuid
from pydantic import BaseModel, EmailStr, Field
from uuid import UUID

# Shared password constraint: minimum length enforced at the API boundary.
PasswordStr = Field(min_length=8, max_length=128)


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = PasswordStr


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    team_id: UUID
    role: str
    enabled_modules: list[str] | None = None

    model_config = {"from_attributes": True}


class UserInviteCreate(BaseModel):
    email: EmailStr
    role: str = "member"


class UserDirectCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = PasswordStr
    role: str = "member"


class UserAcceptInvite(BaseModel):
    token: str
    name: str
    password: str = PasswordStr


class UserManagementResponse(BaseModel):
    id: UUID
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime.datetime
    enabled_modules: list[str] | None = None
    custom_modules: list[str] | None = None

    model_config = {"from_attributes": True}


class PlatformUserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    is_platform_owner: bool = True

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse | PlatformUserResponse
    is_platform_owner: bool = False


# ---- Admin / Org schemas ----

class OrgCreate(BaseModel):
    name: str
    org_key: str
    domain: str
    admin_name: str
    admin_email: EmailStr
    admin_password: str = PasswordStr
    enabled_modules: list[str] | None = None


class OrgResponse(BaseModel):
    id: UUID
    name: str
    org_key: str
    domain: str
    schema_name: str
    enabled_modules: list[str] | None = None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class OrgModulesUpdate(BaseModel):
    enabled_modules: list[str]


class UserModulesUpdate(BaseModel):
    enabled_modules: list[str] | None = None


# ---- Individual User schemas ----

class IndividualUserCreate(BaseModel):
    email: EmailStr


class IndividualUserInviteResponse(BaseModel):
    message: str
    token: str
    invite_url: str


class IndividualUserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    team_id: UUID
    enabled_modules: list[str] | None = None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


# ---- Waitlist schemas ----

class WaitlistCreate(BaseModel):
    email: EmailStr

class WaitlistResponse(BaseModel):
    id: UUID
    email: str
    status: str = "pending"
    created_at: datetime.datetime

    model_config = {"from_attributes": True}

