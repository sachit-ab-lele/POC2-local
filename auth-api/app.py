import os
import pymysql
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
DATABASE_HOST = os.environ.get("DB_HOST", "mysql")
DATABASE_USER = os.environ.get("DB_USER", "voteuser")
DATABASE_PASSWORD = os.environ.get("DB_PASSWORD", "votepassword")
DATABASE_NAME = os.environ.get("DB_NAME", "votelogin_db")

JWT_SECRET_KEY = os.environ.get("JWT_SECRET", "yoursupersecretkey") # Change in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # Not directly used for login, but good for protected routes

# --- Database Connection ---
def get_db_connection():
    try:
        conn = pymysql.connect(
            host=DATABASE_HOST,
            user=DATABASE_USER,
            password=DATABASE_PASSWORD,
            db=DATABASE_NAME,
            cursorclass=pymysql.cursors.DictCursor
        )
        return conn
    except pymysql.MySQLError as e:
        print(f"Database connection failed: {e}") # Replace with proper logging
        raise HTTPException(status_code=500, detail="Database connection error")

# --- Pydantic Models ---
class UserLogin(BaseModel):
    username: str
    password: str
    role: str # 'user' or 'admin'

class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    role: str

# --- Helper Functions ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- API Endpoints ---
@app.post("/login", response_model=Token)
async def login_for_access_token(form_data: UserLogin):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # WARNING: Storing and comparing plain text passwords is INSECURE.
            sql = "SELECT id, username, password, role FROM users WHERE username = %s"
            cursor.execute(sql, (form_data.username,))
            user = cursor.fetchone()

            # WARNING: Comparing plain text passwords is INSECURE.
            if not user or user['password'] != form_data.password:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if user['role'] != form_data.role:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Incorrect role selected. Expected {user['role']}.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": user['username'], "role": user['role'], "id": user['id']},
                expires_delta=access_token_expires
            )
            return {"access_token": access_token, "token_type": "bearer", "role": user['role']}
    except pymysql.MySQLError as e:
        print(f"Database query error: {e}") # Replace with proper logging
        raise HTTPException(status_code=500, detail="Error processing login")
    finally:
        if conn:
            conn.close()

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# WARNING: The 'users' table should now store plain text passwords in the 'password' column.
# This is highly insecure and should only be used for demonstration purposes.
# Example SQL to create table (replace password_hash with password):
# CREATE TABLE IF NOT EXISTS users ( id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(50) UNIQUE NOT NULL, password VARCHAR(255) NOT NULL, role ENUM('user', 'admin') NOT NULL );