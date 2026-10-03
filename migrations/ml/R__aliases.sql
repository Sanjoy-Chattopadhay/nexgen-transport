-- What the ML service reads from analytics: the historic trip store and its
-- summaries, under the names Smart-Truck's ML code uses.
CREATE OR REPLACE VIEW trips AS SELECT * FROM {{schema:analytics}}.v1_trips;
CREATE OR REPLACE VIEW drivers AS SELECT * FROM {{schema:analytics}}.v1_drivers;
CREATE OR REPLACE VIEW vehicles AS SELECT * FROM {{schema:analytics}}.v1_vehicles;
CREATE OR REPLACE VIEW locations AS SELECT * FROM {{schema:analytics}}.v1_locations;
CREATE OR REPLACE VIEW customers AS SELECT * FROM {{schema:analytics}}.v1_customers;
CREATE OR REPLACE VIEW driver_summary AS SELECT * FROM {{schema:analytics}}.v1_driver_summary;
CREATE OR REPLACE VIEW vehicle_summary AS SELECT * FROM {{schema:analytics}}.v1_vehicle_summary;
CREATE OR REPLACE VIEW route_summary AS SELECT * FROM {{schema:analytics}}.v1_route_summary;
CREATE OR REPLACE VIEW route_time_patterns AS SELECT * FROM {{schema:analytics}}.v1_route_time_patterns;
CREATE OR REPLACE VIEW alerts AS SELECT * FROM {{schema:analytics}}.v1_alerts;
