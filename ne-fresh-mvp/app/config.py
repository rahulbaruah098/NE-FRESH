
import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY","change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI","sqlite:///ne_fresh.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEBUG = bool(int(os.getenv("DEBUG","1")))
    SECURE_COOKIES = bool(int(os.getenv("SECURE_COOKIES","1")))
    PAYMENT_SANDBOX = bool(int(os.getenv("PAYMENT_SANDBOX","1")))
    RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT","200/hour")
    DELIVERY_ENABLED = bool(int(os.getenv("DELIVERY_ENABLED","0")))
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL","ites@sdsindia.in")
    PINCODE_ALLOWED = os.getenv("PINCODE_ALLOWED","796009")
