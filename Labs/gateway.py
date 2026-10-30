import os
from datetime import datetime, timedelta
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, status, Query
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt
from pydantic import BaseModel
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

LAB1_URL = os.getenv("LAB1_URL", "http://lab1-service:8001")
LAB2_URL = os.getenv("LAB2_URL", "http://lab2-service:8002")

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# Учетные записи администратора/аналитика для шлюза
ADMIN_USERNAME = os.getenv("ANALYTICS_USER", "admin")
ADMIN_PASSWORD = os.getenv("ANALYTICS_PASSWORD", "admin123")

app = FastAPI(
    title="API Gateway",
    description="Единая точка входа с локальной JWT-авторизацией",
    version="1.0.0",
    docs_url="/docs"
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Проверка jwt токена ---
def verify_jwt(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        # Проверяем подпись и срок действия токена
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Срок действия JWT токена истек"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный JWT токен доступа"
        )


# --- Создание JWT токена ---
@app.post(
    "/api/v1/auth/login",
    response_model=TokenResponse,
    tags=["Аутентификация"],
    summary="Генерация JWT токена"
)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == ADMIN_USERNAME and form_data.password == ADMIN_PASSWORD:
        payload = {
            "sub": form_data.username,
            "role": "analyst",
            "exp": datetime.utcnow() + timedelta(hours=12)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return TokenResponse(access_token=token)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверное имя пользователя или пароль"
    )


# --- Лаба №1 ---

# --- Лаба №1 ---
@app.get(
    "/api/v1/labs/lab1/attendance-report",
    tags=["Лабораторная работа №1"],
    summary="Отчет о посещаемости"
)
async def proxy_lab1_report(
    request: Request,
    term: str = Query(..., description="Термин/фраза для поиска в лекциях"),
    period_start: str = Query(..., description="Начало периода (YYYY-MM-DD)"),
    period_end: str = Query(..., description="Конец периода (YYYY-MM-DD)"),
    min_attendance_pct: float = Query(0.0, description="Минимальный процент"),
    max_attendance_pct: float = Query(100.0, description="Максимальный процент"),
    limit: int = Query(10, description="Лимит топа студентов"),
    _user: dict = Depends(verify_jwt)
):
    # Явно собираем все параметры в словарь для отправки
    params = dict(request.query_params)
    auth_header = request.headers.get("Authorization")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{LAB1_URL}/api/v1/labs/lab1/attendance-report",
                params=params,
                headers={"Authorization": auth_header} if auth_header else {}
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Сервис Лаб №1 недоступен: {str(e)}")

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        return response.json()


# ---Лаба №2 ---
@app.get(
    "/api/v1/labs/lab2/audience-report",
    tags=["Лабораторная работа №2"],
    summary="Отчет об объемах аудиторий и тех. средствах"
)
async def proxy_lab2_report(
    request: Request,
    semester: int = Query(..., description="Семестр обучения"),
    enrollment_year: int = Query(..., description="Год поступления"),
    _user: dict = Depends(verify_jwt)
):
    params = dict(request.query_params)
    auth_header = request.headers.get("Authorization")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{LAB2_URL}/api/v1/labs/lab2/audience-report",
                params=params,
                headers={"Authorization": auth_header} if auth_header else {}
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Сервис Лаб №2 недоступен: {str(e)}")

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        return response.json()