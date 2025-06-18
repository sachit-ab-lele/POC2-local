-- This script will run after the MySQL container has initialized the database
-- and user specified by MYSQL_DATABASE, MYSQL_USER, and MYSQL_PASSWORD environment variables.

-- The MySQL client executing this script is typically already connected to the
-- database specified by the MYSQL_DATABASE environment variable.
-- If not, uncomment the line below and replace `your_app_db` with the actual database name
-- from your `mysql-creds` secret (the value for `mysql-database`).
-- USE `your_app_db`;

CREATE TABLE IF NOT EXISTS credentials (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL, -- IMPORTANT: Store securely hashed passwords!
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'user')),
    INDEX(username)
);

-- Insert an admin user.
-- TODO: Replace 'your_securely_hashed_admin_password' with an actual secure hash.
-- For example, in Python using Werkzeug:
-- from werkzeug.security import generate_password_hash
-- admin_hash = generate_password_hash('admin_password_goes_here')
INSERT IGNORE INTO credentials (username, password_hash, role)
VALUES ('admin', 'your_securely_hashed_admin_password', 'admin');

-- Insert a regular user.
-- TODO: Replace 'your_securely_hashed_user_password' with an actual secure hash.
-- user_hash = generate_password_hash('user_password_goes_here')
INSERT IGNORE INTO credentials (username, password_hash, role)
VALUES ('user', 'your_securely_hashed_user_password', 'user');

-- FLUSH PRIVILEGES; -- Usually not needed when scripts are run by docker-entrypoint
