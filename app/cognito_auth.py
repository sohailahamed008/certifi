import os
import requests
from jose import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

AWS_REGION = os.getenv("AWS_REGION")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")

if not AWS_REGION or not USER_POOL_ID or not CLIENT_ID:
    raise RuntimeError("Cognito environment variables not set")

JWKS_URL = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"

security = HTTPBearer()

# ✅ Fetch JWKS ONLY ONCE (huge performance improvement)
try:
    JWKS = requests.get(JWKS_URL, timeout=10).json()
except Exception as e:
    print("Failed to fetch Cognito JWKS:", e)
    JWKS = {"keys": []}


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials

    try:
        # Get token header
        header = jwt.get_unverified_header(token)

        # Find correct key from cached JWKS
        key = None
        for k in JWKS["keys"]:
            if k["kid"] == header["kid"]:
                key = k
                break

        if not key:
            raise HTTPException(status_code=401, detail="Public key not found")

        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}",
        )

        # -------------------------
        # ROLE LOGIC
        # -------------------------
        email = payload.get("email")

        if email and email.endswith("@nmkglobalinc.com"):
            payload["role"] = "admin"
        else:
            payload["role"] = "user"

        return payload

    except Exception as e:
        print("JWT ERROR:", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )