"""[MOD] 登录认证路由：用户名密码 → 签名令牌（角色 × 部门）。

POST /api/auth/login  登录，返回 token 与用户信息
GET  /api/auth/me     校验令牌，返回当前用户信息
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.security.users import authenticate, issue_token, verify_token

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
async def me(authorization: str | None = None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = verify_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return {"user": user}
