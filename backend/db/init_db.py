from backend.db.database import engine
from backend.db.models import Base
from backend.db import models

Base.metadata.create_all(bind=engine)

print("Tables created successfully")