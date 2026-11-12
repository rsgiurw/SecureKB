"""[MOD] 登录认证路由：用户名密码 → 签名令牌（角色 × 部门）。

POST /api/auth/login  登录，返回 token 与用户信息
GET  /api/auth/me     校验令牌，返回当前用户信息
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.security.users import authenticate, change_password, issue_token, verify_token

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
async def login(req: LoginRequest) -> dict:
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": issue_token(user["username"]),
        "user": user,
    }


@router.get("/me")
async def me(authorization: str | None = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = verify_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return {"user": user}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


@router.put("/change-password")
async def change_password_endpoint(
    req: ChangePasswordRequest, authorization: str | None = Header(None)
) -> dict:
    """[MOD EXP-023] 用户自助修改密码（需验证旧密码）。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = verify_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    ok, error = change_password(user["username"], req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=error)
    return {"success": True, "message": "密码修改成功"}
