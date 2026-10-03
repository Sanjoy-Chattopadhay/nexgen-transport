-- Analytics' published contract: the historic trip store and its summaries,
-- read by the ML service.
CREATE OR REPLACE VIEW v1_trips AS SELECT * FROM trips;
CREATE OR REPLACE VIEW v1_drivers AS SELECT * FROM drivers;
CREATE OR REPLACE VIEW v1_vehicles AS SELECT * FROM vehicles;
CREATE OR REPLACE VIEW v1_locations AS SELECT * FROM locations;
CREATE OR REPLACE VIEW v1_customers AS SELECT * FROM customers;
CREATE OR REPLACE VIEW v1_driver_summary AS SELECT * FROM driver_summary;
CREATE OR REPLACE VIEW v1_vehicle_summary AS SELECT * FROM vehicle_summary;
CREATE OR REPLACE VIEW v1_route_summary AS SELECT * FROM route_summary;
CREATE OR REPLACE VIEW v1_customer_summary AS SELECT * FROM customer_summary;
CREATE OR REPLACE VIEW v1_route_time_patterns AS SELECT * FROM route_time_patterns;
CREATE OR REPLACE VIEW v1_daily_fleet_stats AS SELECT * FROM daily_fleet_stats;
CREATE OR REPLACE VIEW v1_waypoints AS SELECT * FROM waypoints;
CREATE OR REPLACE VIEW v1_alerts AS SELECT * FROM alerts;
