-- This script will be executed when the MySQL container starts for the first time.

-- Ensure the correct database is selected.
-- The database 'votelogin_db' should be automatically created by the
-- MYSQL_DATABASE environment variable in docker-compose.yml.
USE votelogin_db;

-- Create the 'users' table if it doesn't already exist.
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL, -- WARNING: Storing plain text passwords is INSECURE.
    role ENUM('user', 'admin') NOT NULL
);

-- Insert sample users. Replace with your desired credentials.
-- WARNING: Storing plain text passwords is INSECURE and not recommended for production.
INSERT INTO users (username, password, role) VALUES ('testuser', 'userpass123', 'user') ON DUPLICATE KEY UPDATE password=VALUES(password), role=VALUES(role);
INSERT INTO users (username, password, role) VALUES ('admin01', 'adminpass123', 'admin') ON DUPLICATE KEY UPDATE password=VALUES(password), role=VALUES(role);