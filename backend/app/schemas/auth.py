from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# bcrypt hashes at most the first 72 BYTES of a password and silently ignores
# the rest (verified against bcrypt 4.2.1: it truncates, it does not raise). So
# anything past 72 bytes is decoration, and the 128 cap is about bounding the
# request rather than the secret.
PASSWORD_MAX_LENGTH = 128


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=PASSWORD_MAX_LENGTH)
    risk_profile: str | None = Field(default=None, max_length=50)


class LoginRequest(BaseModel):
    email: EmailStr
    # Capped like the register field. Unbounded here meant a login body could
    # carry an arbitrarily large string that the server decoded and passed to
    # bcrypt before rejecting it — work done on behalf of an anonymous caller,
    # per request. Validation now rejects it at parse time with a 422.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    risk_profile: str | None
    created_at: datetime
