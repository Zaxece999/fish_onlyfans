import logging
import datetime
import uuid
from sqlalchemy import create_engine, Column, String, Text, DateTime, Boolean, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT


db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(db_url)

Base = declarative_base()

class Visitor(Base):
    __tablename__ = 'visitors'

    id = Column(Integer, primary_key=True) 
    id_string = Column(String(50), unique=True)
    phone_number = Column(String(20))
    code = Column(String(10))
    ip_address = Column(String(45))
    user_agent = Column(Text)
    timestamp = Column(DateTime, default=datetime.datetime.now)
    has_phone_number = Column(Boolean, default=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_visitor(db, ip_address: str, user_agent: str, visitor_id: str):
    new_visitor = Visitor(
        id_string=visitor_id,
        ip_address=ip_address,
        user_agent=user_agent,
        timestamp=datetime.datetime.now()
    )
    db.add(new_visitor)
    db.commit()
    db.refresh(new_visitor)
    logging.info(f"New visitor record created with ID: {new_visitor.id} (String ID: {new_visitor.id_string})")
    return new_visitor

def get_visitor_by_id_string(db, visitor_id: str):
     return db.query(Visitor).filter(Visitor.id_string == visitor_id).first()


def update_visitor_phone(db, visitor_id: str, phone_number: str):
    visitor = get_visitor_by_id_string(db, visitor_id)
    if visitor:
        visitor.phone_number = phone_number
        visitor.has_phone_number = True
        db.commit()
        db.refresh(visitor)
        logging.info(f"Phone number updated for user ID: {visitor.id} (String ID: {visitor.id_string})")
        return visitor
    return None

def update_visitor_code(db, visitor_id: str, code: str):
    visitor = get_visitor_by_id_string(db, visitor_id)
    if visitor:
        visitor.code = code
        db.commit()
        db.refresh(visitor)
        logging.info(f"Code updated for user ID: {visitor.id} (String ID: {visitor.id_string})")
        return visitor
    return None 