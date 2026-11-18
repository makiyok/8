from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()


# SQLite は既定だと「読んでから書く」間に別の書き込みが割り込める。
# 予約の定員チェックを確実にするため、SQLite のときだけトランザクションを
# BEGIN IMMEDIATE（開始時に書き込みロックを取る）で始める。
# 本番の MySQL / PostgreSQL では、booking.py の with_for_update() が行ロックを取る。
@event.listens_for(Engine, "connect")
def _sqlite_connect(dbapi_connection, connection_record):
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        dbapi_connection.isolation_level = None


@event.listens_for(Engine, "begin")
def _sqlite_begin(conn):
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN IMMEDIATE")
