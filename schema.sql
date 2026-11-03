CREATE DATABASE IF NOT EXISTS medicine_assistant
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE medicine_assistant;

CREATE TABLE IF NOT EXISTS pharmacy (
    pharmacy_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    address VARCHAR(300) NOT NULL,
    contact VARCHAR(30) NOT NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    opening_hours VARCHAR(150) NULL
);

CREATE TABLE IF NOT EXISTS medicine (
    medicine_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    strength VARCHAR(50) DEFAULT '',
    form VARCHAR(50) DEFAULT '',
    generic_group VARCHAR(100) NULL,
    INDEX idx_medicine_name (name),
    INDEX idx_generic_group (generic_group)
);

CREATE TABLE IF NOT EXISTS inventory (
    inventory_id INT AUTO_INCREMENT PRIMARY KEY,
    medicine_id INT NOT NULL,
    pharmacy_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 0,
    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
       ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_inventory_medicine FOREIGN KEY (medicine_id)
       REFERENCES medicine(medicine_id),
    CONSTRAINT fk_inventory_pharmacy FOREIGN KEY (pharmacy_id)
       REFERENCES pharmacy(pharmacy_id),
    CONSTRAINT uq_pharmacy_medicine UNIQUE (pharmacy_id, medicine_id),
    CONSTRAINT chk_quantity_nonnegative CHECK (quantity >= 0)
);

CREATE TABLE IF NOT EXISTS alternative_mapping (
    mapping_id INT AUTO_INCREMENT PRIMARY KEY,
    medicine_id INT NOT NULL,
    generic_group VARCHAR(100),
    verified_alternative VARCHAR(150) NOT NULL,
    review_status ENUM('pending','verified','rejected') DEFAULT 'pending',
    FOREIGN KEY (medicine_id) REFERENCES medicine(medicine_id)
);

CREATE TABLE IF NOT EXISTS consumption_history (
    history_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    medicine_id INT NOT NULL,
    pharmacy_id INT NOT NULL,
    date DATE NOT NULL,
    units_dispensed INT NOT NULL DEFAULT 0,
    FOREIGN KEY (medicine_id) REFERENCES medicine(medicine_id),
    FOREIGN KEY (pharmacy_id) REFERENCES pharmacy(pharmacy_id),
    INDEX idx_consumption_date (date)
);

-- Login accounts for chemists. Passwords are stored as salted hashes by the app.
CREATE TABLE IF NOT EXISTS app_user (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    pharmacy_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pharmacy_id) REFERENCES pharmacy(pharmacy_id)
);

-- Optional sample medicine rows for testing.
INSERT INTO medicine (name, strength, form, generic_group)
SELECT 'Paracetamol', '500mg', 'Tablet', 'Paracetamol'
WHERE NOT EXISTS (SELECT 1 FROM medicine WHERE name='Paracetamol' AND strength='500mg' AND form='Tablet');

INSERT INTO medicine (name, strength, form, generic_group)
SELECT 'Cetirizine', '10mg', 'Tablet', 'Cetirizine'
WHERE NOT EXISTS (SELECT 1 FROM medicine WHERE name='Cetirizine' AND strength='10mg' AND form='Tablet');
