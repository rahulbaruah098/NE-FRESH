import importlib
import os
import sys
from pathlib import Path
import pytest

@pytest.fixture(scope="session")
def runtime_modules(tmp_path_factory):
    pytest.importorskip("flask")
    mongomock = pytest.importorskip("mongomock")
    pytest.importorskip("pymongo")
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    upload_dir = tmp_path_factory.mktemp("uploads")
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("APP_SECRET_KEY", "step2-test-secret-key-0123456789abcdef")
    os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/NE_Fresh_Test")
    os.environ.setdefault("ENABLE_CSRF_PROTECTION", "0")
    os.environ.setdefault("UPLOAD_FOLDER", str(upload_dir))
    os.environ.setdefault("NEFRESH_AUTO_RELOAD", "0")

    app_module = importlib.import_module("app")
    app_core = importlib.import_module("app_core")
    fake_client = mongomock.MongoClient()
    fake_db = fake_client["NE_Fresh_Test"]

    # Route modules copied the mongo object via `from app_core import *`. Patch
    # every loaded module that owns such a global so all code uses one fixture DB.
    app_core.mongo = fake_db
    mongo_db = importlib.import_module("mongo_db")
    mongo_db.mongo = fake_db
    for name, mod in list(sys.modules.items()):
        if (name.startswith("routes.") or name.startswith("services.")) and mod is not None and hasattr(mod, "mongo"):
            setattr(mod, "mongo", fake_db)

    # Step 5 services import the shared Mongo handle directly from extensions.
    # Patch the module too so any service imported after fixture setup receives
    # the same isolated mongomock database.
    extensions = importlib.import_module("extensions")
    extensions.mongo = fake_db

    app = app_module.app
    app.config.update(TESTING=True, ENABLE_CSRF_PROTECTION=False, UPLOAD_FOLDER=str(upload_dir))
    return {"app": app, "db": fake_db, "root": root, "upload_dir": upload_dir}

@pytest.fixture
def runtime_app(runtime_modules):
    db = runtime_modules["db"]
    # Drop test data between tests while retaining the fake DB object patched into modules.
    for name in db.list_collection_names():
        db.drop_collection(name)
    return runtime_modules

@pytest.fixture
def client(runtime_app):
    return runtime_app["app"].test_client()

@pytest.fixture
def bson_object_id():
    bson = pytest.importorskip("bson")
    return bson.ObjectId


def login_as(client, db, ObjectId, role, **extra):
    uid = ObjectId()
    doc = {"_id": uid, "name": role.title() + " Test", "email": f"{uid}@example.test", "role": role, "is_active": 1}
    doc.update(extra)
    db.users.insert_one(doc)
    with client.session_transaction() as sess:
        sess["user_id"] = str(uid)
    return doc
