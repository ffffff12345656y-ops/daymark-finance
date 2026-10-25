# Daymark · Habit & Goal Tracker

เว็บแอปติดตามเป้าหมายรายวัน เขียนด้วย Flask, SQLite และ JavaScript หน้าเว็บดึงข้อมูลจากฐานข้อมูลจริง และสร้างตาราง/กราฟตามเป้าหมายที่ผู้ใช้กำหนด

## เริ่มใช้งาน

```bash
cd /home/nut/Pro
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

เปิด `http://127.0.0.1:5000` ฐานข้อมูล `habits.db` จะถูกสร้างอัตโนมัติในโฟลเดอร์นี้ สามารถกำหนดตำแหน่งอื่นด้วย environment variable `HABIT_DB` และกำหนดพอร์ตด้วย `PORT` ได้

## บัญชีผู้ใช้และการย้ายข้อมูลเดิม

สมัครบัญชีผ่าน `/register` และเข้าสู่ระบบผ่าน `/login` ข้อมูล Habit และ Student ถูกกรองตามบัญชีที่ล็อกอินอยู่ รหัสผ่านจัดเก็บเป็น hash; session cookie ตั้งค่า `HttpOnly` และ `SameSite=Lax` พร้อมตรวจ CSRF token สำหรับคำขอที่เปลี่ยนข้อมูล

แอปสร้าง Flask signing key แบบสุ่มไว้ใน `instance/secret_key` (permission เฉพาะเจ้าของไฟล์) เพื่อให้ session ยังใช้ได้หลัง restart; ไฟล์นี้ถูก ignore จาก Git หาก deploy หลาย instance ให้กำหนด `SECRET_KEY` เดียวกันผ่าน environment แทน:

```bash
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
.venv/bin/python app.py
```

เมื่อใช้ HTTPS tunnel หรือ HTTPS deployment ให้ตั้ง `SESSION_COOKIE_SECURE=1`; หากทดสอบผ่าน HTTP บนเครื่องให้คงค่าเริ่มต้นไว้

## สำรองและคืนค่าข้อมูล

ในหน้า Habit และ Student มีปุ่มดาวน์โหลดไฟล์ JSON ส่วนตัวและอัปโหลดไฟล์เพื่อคืนค่า การนำเข้าตรวจ version, โครงสร้าง, จำนวนรายการ, ค่า และความสัมพันธ์ก่อนเขียน; เมื่อยืนยันแล้วจะแทนที่ข้อมูลของบัญชีที่ล็อกอินอยู่ทั้งสองโหมด โดยไม่กระทบบัญชีอื่น ไฟล์จำกัดขนาด 5 MB

ทุกครั้งที่แอปเริ่ม จะสร้าง snapshot ล่าสุดของ SQLite ทั้งสองฐานข้อมูลไว้ใน `backups/database_backup.db` และ `backups/student_database_backup.db` โดยใช้ SQLite backup API; ไฟล์เดิมจะถูกแทนที่เมื่อเริ่มครั้งถัดไป โฟลเดอร์ถูกตั้ง permission เป็น `0700` และไฟล์เป็น `0600` จึงควรสำรองโฟลเดอร์นี้ออกไปยังพื้นที่อื่นเป็นระยะ และไม่วางไว้ใน `static/` หรือเผยแพร่ผ่าน URL

ข้อมูลเก่าใน `habits.db` และ `student.db` จะยังไม่มีเจ้าของหลัง migration จนกว่าจะกำหนดบัญชีให้โดยชัดเจน ก่อนเริ่มให้หยุดแอปและสำรองฐานข้อมูลทั้งสองไฟล์:

```bash
cp habits.db habits.db.backup
cp student.db student.db.backup
```

จากนั้นเริ่มแอป สมัครบัญชีของเจ้าของข้อมูลเดิม แล้วหยุดแอปและรันคำสั่งนี้หนึ่งครั้ง โดยแทน `USERNAME` ด้วยชื่อบัญชี:

```bash
.venv/bin/flask --app app assign-legacy-data USERNAME
```

คำสั่งนี้ผูกข้อมูลที่ยังไม่มีเจ้าของทั้งหมดจากทั้งสองฐานข้อมูลเข้ากับบัญชีนั้น จึงควรรันก่อนแชร์ URL หรือเปิดให้ผู้ใช้อื่นสมัคร ถ้าข้อมูลเดิมไม่มีหรือไม่ต้องการเก็บไว้ ไม่ต้องรันคำสั่งนี้ Student Mode คงฐานข้อมูลแยกไว้ จึงมีตาราง `users` ที่ใช้ ID เดียวกับฐานข้อมูลหลักเพื่อให้ Foreign Key อยู่ใน SQLite ไฟล์เดียวกัน

## แชร์แอปผ่าน Public URL

แอปต้องกำลังรันอยู่ก่อน แล้วเปิด tunnel ไปยังพอร์ต 5000 ในอีก terminal หนึ่ง ไม่ต้องเปลี่ยน Flask ให้ bind กับ `0.0.0.0`

### ngrok

1. ติดตั้ง ngrok จาก [หน้า Download](https://ngrok.com/download) และสร้างบัญชี
2. เพิ่ม authtoken ในเครื่อง (ไม่ต้องใส่ token ลงในโปรเจกต์):

	```bash
	ngrok config add-authtoken YOUR_AUTHTOKEN
	```

3. Terminal แรก: รันแอปด้วย `.venv/bin/python app.py` หรือคำสั่งเริ่มแอปที่ใช้อยู่
4. Terminal ที่สอง: เปิด tunnel

	```bash
	ngrok http 5000
	```

คัดลอก HTTPS URL ที่ ngrok แสดง เช่น `https://example.ngrok-free.app` แล้วส่งให้เพื่อน เมื่อหยุด ngrok ด้วย `Ctrl+C` URL จะใช้ไม่ได้อีก

### LocalTunnel

ต้องติดตั้ง Node.js และ npm ก่อน จากนั้นเปิดแอปใน terminal แรก แล้วรันใน terminal ที่สอง:

```bash
npx localtunnel --port 5000
```

แชร์ URL ที่คำสั่งแสดง และหยุด tunnel ด้วย `Ctrl+C`

**ข้อควรระวัง:** Public URL เปิดให้เข้าถึงหน้าเว็บและ API จากอินเทอร์เน็ต ผู้ใช้ต้องสมัครบัญชีเพื่อเข้าถึงข้อมูลส่วนตัว แต่ยังไม่มีระบบยืนยันอีเมล, จำกัดอัตราการลองรหัสผ่าน หรือขั้นตอนกู้คืนบัญชี ใช้เพื่อทดสอบชั่วคราวและหยุด tunnel เมื่อเลิกแชร์ ไม่ใช่การ deploy production

## Student Mode

เลือก **โหมดนักเรียน** จากหน้า Habit Tracker หรือเปิด `http://127.0.0.1:5000/student` โดยตรง ข้อมูลนักเรียนเก็บแยกใน `student.db` (กำหนด path ด้วย `STUDENT_DB`) ไม่ใช้ตารางหรือ API ของ Habit Tracker

โหมดนักเรียนมีงาน/การบ้านและสถานะ, ตารางเรียนวันจันทร์–ศุกร์, รายวิชาและโน้ต, คะแนนพร้อม GPA ถ่วงน้ำหนักตามหน่วยกิต, เป้าหมาย GPA, วันสอบนับถอยหลัง และปฏิทินที่รวมวันสอบ/กำหนดส่ง/วันสำคัญ แต่ละรายการแก้ไขและลบได้ โดยการลบมี confirm dialog

การลบเป้าหมายใน Habit Mode จะ archive เป้าหมายออกจากรายการใช้งานเพื่อเก็บประวัติเช็กลิสต์และ progress เดิมไว้ การแก้ชื่อยังใช้ `PATCH` หรือ `PUT /api/goals/<id>` และการลบใช้ `DELETE /api/goals/<id>`

API ของ Student Mode อยู่ใต้ `/api/student/`: สร้างงานด้วย `{ "subjectId": 1, "title": "เรียงความ", "dueDate": "2026-10-20" }`; assignments, schedules, subjects, notes, grades, exams และ calendar events รองรับเพิ่ม/แก้ไข (`PATCH`/`PUT`)/ลบ; งานผูกกับ `subjects.id` และไม่อนุญาตให้ลบวิชาที่ยังมีงานอ้างอิง พร้อม `PUT /settings/target-gpa` และ `GET /dashboard?month=YYYY-MM`

## API

- `GET /api/dashboard?month=YYYY-MM` อ่านเป้าหมาย เช็กลิสต์ และ metrics ของเดือน
- `POST /api/goals` เพิ่มเป้าหมายด้วย `{ "name": "อ่านหนังสือ", "color": "#75d6b1" }`
- `PATCH /api/goals/<id>` แก้ชื่อหรือสีเป้าหมาย
- `PUT /api/goals/<id>/monthly-progress` บันทึกเปอร์เซ็นต์ (0–100) และโน้ตของเป้าหมายในเดือนที่เลือกด้วย `{ "month": "2026-10", "percent": 73, "note": "ทำโปรเจกต์ถึงบทที่ 3" }`; ข้อมูลของแต่ละเป้าหมายและแต่ละเดือนแยกกัน
- `DELETE /api/goals/<id>` ลบเป้าหมายและประวัติเช็กลิสต์ของเป้าหมายนั้น
- `PUT /api/completions` บันทึกสถานะด้วย `{ "goalId": 1, "date": "2026-10-14", "done": true }`
- `PUT /api/metrics` บันทึกวัน คะแนนใจ/พลังงาน (1–5 หรือ `null`) และโน้ต

Tailwind CSS และ Chart.js โหลดผ่าน CDN จึงต้องเชื่อมต่ออินเทอร์เน็ตเพื่อใช้สไตล์/กราฟครบถ้วน; Flask และข้อมูล SQLite ทำงานในเครื่อง