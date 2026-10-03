from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, Numeric, Boolean,
    ForeignKey, JSON, Date, SmallInteger, TIMESTAMP, Index,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    mobile1 = Column(String(20))
    mobile2 = Column(String(20))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    trips = relationship("Trip", back_populates="driver", lazy="dynamic")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(String(50), nullable=False, unique=True)
    asset_type = Column(String(100))
    trailer_type = Column(String(100))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    trips = relationship("Trip", back_populates="vehicle", lazy="dynamic")


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cne_name = Column(String(255))
    cne_id = Column(Integer)
    cust_login_id = Column(String(255), unique=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)


class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dispatch_entry_no = Column(String(100), nullable=False, unique=True)

    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id", ondelete="SET NULL"))
    origin_id = Column(Integer, ForeignKey("locations.id", ondelete="SET NULL"))
    destination_id = Column(Integer, ForeignKey("locations.id", ondelete="SET NULL"))
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"))

    trip_start = Column(DateTime)
    trip_end = Column(DateTime)
    trip_eta = Column(DateTime)
    ata_in = Column(DateTime)
    ata_out = Column(DateTime)
    # When the trip RECORD was closed administratively; distinct from the
    # arrival (ata_in / trip_end), which stays NULL for trips that never ran.
    trip_closed_at = Column(DateTime)
    dt_created = Column(DateTime)
    dt_updated = Column(DateTime)

    trip_km = Column(Numeric(10, 2))
    total_dist = Column(Numeric(10, 2))
    cover_dist = Column(Numeric(10, 2))

    trip_duration_minutes = Column(Numeric(12, 2))
    eta_met = Column(Boolean)
    eta_delay_minutes = Column(Numeric(12, 2))
    avg_speed_kmph = Column(Numeric(8, 2))

    trip_status = Column(String(20))
    is_active = Column(String(5))
    trip_close_remark = Column(Text)
    material_desc = Column(String(500))
    invoice_no = Column(String(100))
    invoice_date = Column(String(50))
    ref_no = Column(String(100))
    ref_date = Column(String(50))
    entry_type = Column(String(50))
    own_market_type = Column(String(50))
    running_sts = Column(String(50))
    trip_seq = Column(Integer)
    delay_by = Column(Numeric(10, 2))

    device_id = Column(String(100))
    device_type = Column(String(50))

    entity_id = Column(Integer)
    cnr_id = Column(Integer)
    created_by = Column(String(100))
    updated_by = Column(String(100))
    track_link = Column(Text)
    data_string = Column(Text)

    weather_conditions = Column(String(100))
    fuel_consumed = Column(Numeric(10, 2))
    load_weight_kg = Column(Numeric(10, 2))

    driver = relationship("Driver", back_populates="trips")
    vehicle = relationship("Vehicle", back_populates="trips")
    origin = relationship("Location", foreign_keys=[origin_id])
    destination = relationship("Location", foreign_keys=[destination_id])
    customer = relationship("Customer")


class Waypoint(Base):
    __tablename__ = "waypoints"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id", ondelete="SET NULL"))
    trip_id = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"))
    latitude = Column(Numeric(10, 7))
    longitude = Column(Numeric(10, 7))
    speed_kmph = Column(Numeric(8, 2))
    status = Column(String(100))
    location_text = Column(Text)
    distance_from_prev = Column(Numeric(10, 2))
    recorded_at = Column(DateTime, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)


class FileUpload(Base):
    __tablename__ = "file_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)
    file_type = Column(String(10), nullable=False)
    file_size_bytes = Column(BigInteger)
    upload_type = Column(String(50), nullable=False)
    status = Column(String(20), default="pending")
    total_records = Column(Integer)
    records_processed = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)
    error_summary = Column(JSON)
    uploaded_at = Column(TIMESTAMP, default=datetime.utcnow)
    processing_started_at = Column(DateTime)
    processing_completed_at = Column(DateTime)


class MLModel(Base):
    __tablename__ = "ml_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)
    model_type = Column(String(50), nullable=False)
    target_variable = Column(String(100), nullable=False)
    metrics = Column(JSON)
    feature_columns = Column(JSON)
    hyperparameters = Column(JSON)
    model_artifact_path = Column(String(500))
    training_data_count = Column(Integer)
    training_data_start = Column(DateTime)
    training_data_end = Column(DateTime)
    is_active = Column(Boolean, default=False)
    trained_at = Column(TIMESTAMP, default=datetime.utcnow)
    notes = Column(Text)


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("ml_models.id", ondelete="CASCADE"), nullable=False)
    trip_id = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"))
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"))
    input_features = Column(JSON, nullable=False)
    predicted_value = Column(Numeric(12, 4))
    prediction_type = Column(String(50), nullable=False)
    confidence_score = Column(Numeric(5, 4))
    actual_value = Column(Numeric(12, 4))
    prediction_error = Column(Numeric(12, 4))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String(30), nullable=False)
    severity = Column(String(10), nullable=False)
    trip_id = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"))
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id", ondelete="SET NULL"))
    title = Column(String(255), nullable=False)
    message = Column(Text)
    metadata = Column(JSON)
    is_read = Column(Boolean, default=False)
    is_acknowledged = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    acknowledged_at = Column(DateTime)


# Summary tables (read-only from app perspective, refreshed by migration/scripts)
class DriverSummary(Base):
    __tablename__ = "driver_summary"

    driver_id = Column(Integer, ForeignKey("drivers.id"), primary_key=True)
    driver_name = Column(String(255))
    driver_mobile = Column(String(20))
    total_trips = Column(Integer, default=0)
    eta_met_count = Column(Integer, default=0)
    eta_success_rate = Column(Numeric(6, 2), default=0)
    avg_duration_min = Column(Numeric(12, 2))
    max_duration_min = Column(Numeric(12, 2))
    min_duration_min = Column(Numeric(12, 2))
    avg_speed_kmph = Column(Numeric(8, 2))
    vehicles_used = Column(Integer, default=0)
    total_distance_km = Column(Numeric(14, 2))
    avg_distance_km = Column(Numeric(10, 2))
    avg_eta_delay_min = Column(Numeric(12, 2))
    last_refreshed = Column(TIMESTAMP)


class RouteSummary(Base):
    __tablename__ = "route_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    origin = Column(String(255))
    destination = Column(String(255))
    route_name = Column(String(510))
    trip_count = Column(Integer, default=0)
    avg_duration_min = Column(Numeric(12, 2))
    eta_success_rate = Column(Numeric(6, 2))
    avg_distance_km = Column(Numeric(10, 2))
    last_refreshed = Column(TIMESTAMP)


class VehicleSummary(Base):
    __tablename__ = "vehicle_summary"

    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), primary_key=True)
    asset_id = Column(String(50))
    asset_type = Column(String(100))
    total_trips = Column(Integer, default=0)
    drivers_used = Column(Integer, default=0)
    avg_speed_kmph = Column(Numeric(8, 2))
    total_distance_km = Column(Numeric(14, 2))
    avg_distance_km = Column(Numeric(10, 2))
    eta_success_rate = Column(Numeric(6, 2))
    last_refreshed = Column(TIMESTAMP)


class DailyFleetStats(Base):
    __tablename__ = "daily_fleet_stats"

    stat_date = Column(Date, primary_key=True)
    total_trips = Column(Integer, default=0)
    total_distance_km = Column(Numeric(14, 2))
    avg_speed = Column(Numeric(8, 2))
    eta_success_rate = Column(Numeric(6, 2))
    active_drivers = Column(Integer, default=0)
    active_vehicles = Column(Integer, default=0)
    last_refreshed = Column(TIMESTAMP)


class RouteTimePattern(Base):
    __tablename__ = "route_time_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    origin = Column(String(255))
    destination = Column(String(255))
    hour_of_day = Column(SmallInteger)
    day_of_week = Column(SmallInteger)
    avg_duration = Column(Numeric(12, 2))
    trip_count = Column(Integer, default=0)
    eta_success_rate = Column(Numeric(6, 2))
    last_refreshed = Column(TIMESTAMP)
