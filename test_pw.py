import sqlite3, bcrypt
c = sqlite3.connect(r'D:\MoA Gateway Pro\data\data\config.db')
h = c.execute("SELECT password_hash FROM admin_users WHERE username='admin'").fetchone()[0]
print('Stored hash:', h[:30] + '...')
print('Test 1 matches TestPassword123!:', bcrypt.checkpw(b'TestPassword123!', h.encode()))
print('Test 2 matches admin:', bcrypt.checkpw(b'admin', h.encode()))
print('Test 3 matches wrong:', bcrypt.checkpw(b'wrong', h.encode()))
