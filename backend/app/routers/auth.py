from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..ratelimit import clear, client_ip, record_failure, retry_after
from ..models import User
from ..schemas import LoginRequest, PasswordChange, TokenOut, UserOut
from ..security import create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = payload.username.strip()
    keys = [f"user:{username.lower()}", f"ip:{client_ip(request)}"]

    wait = retry_after(keys)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {wait} 秒后再试",
            headers={"Retry-After": str(wait)},
        )

    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        record_failure(keys)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        record_failure(keys)
        raise HTTPException(status_code=403, detail="该账号已被停用")

    clear(keys)
    return TokenOut(access_token=create_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/password")
def change_password(
    payload: PasswordChange,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}
