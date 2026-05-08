CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    sub VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    session_token VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS gestures (
    id SERIAL PRIMARY KEY,
    gesture_name VARCHAR(255) NOT NULL UNIQUE,
    gesture_description TEXT
);

CREATE TABLE IF NOT EXISTS apps (
    id SERIAL PRIMARY KEY,
    app_name VARCHAR(255) NOT NULL UNIQUE,
    link_to_website TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS user_app_gesture_action (
    id SERIAL PRIMARY KEY,
    app_id INT NOT NULL,
    user_id INT NOT NULL,
    gesture_id INT NOT NULL,
    macro_action VARCHAR(255) NOT NULL,

    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (app_id) REFERENCES apps(id),
    FOREIGN KEY (gesture_id) REFERENCES gestures(id),

    UNIQUE(user_id, app_id, gesture_id)
);
