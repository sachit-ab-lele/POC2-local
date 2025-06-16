from werkzeug.security import generate_password_hash

# For the admin user
admin_password = "adminpassword"
hashed_admin_password = generate_password_hash(admin_password)
print(f"Admin Hashed Password: {hashed_admin_password}")

# For the regular user
user_password = "userpassword"
hashed_user_password = generate_password_hash(user_password)
print(f"User Hashed Password: {hashed_user_password}")
