import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.db.database import engine
from backend.db.models import Base
from backend.db import models
from backend.db.schema_upgrade import upgrade_schema

Base.metadata.create_all(bind=engine)
upgrade_schema(engine)

print("Tables created successfully")