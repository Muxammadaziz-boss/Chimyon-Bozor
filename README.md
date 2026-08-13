# 🏔️🛍️ Chimyon-bozor — Onlayn Do'kon Platformasi

![Chimyon-bozor Logo](static/assets/images/logo/logo.png)

**Chimyon-bozor** — Django va zamonaviy web texnologiyalari (HTML5, Vanilla CSS3, JavaScript ES6) asosida qurilgan premium elektron tijorat (E-commerce) web-platformasi. Platforma foydalanuvchilar va administratorlar uchun qulay, xavfsiz hamda yuqori samaradorlikdagi xarid tajribasini taqdim etadi.

---

## ✨ Asosiy Imkoniyatlar va Xususiyatlar

### 🔐 1. Ro'yxatdan o'tish va Autentifikatsiya (Auth System)
- **O'zbekiston telefon raqamlari formati**: Majburiy `+998` kodi, avtomatik guruhlash (`91 791 48 81`) va 9 ta raqam chegarasi.
- **Jonli bandlik tekshiruvi (Real-Time Live Check)**:
  - Foydalanuvchi nomi (`username`) kamida 4 ta belgilardan iborat bo'lishi va real-vaqtda ochiqligi (available) tekshiriladi.
  - Telefon raqami real-vaqtda bazadagi takrorlanmasligi tekshiriladi.
- **Animatsiyali Parol Mustahkamligi O'lchagichi (Password Strength Meter)**: Parol uzunligi, harf, raqam va maxsus belgilarni real-vaqtda progress-bar hamda indikatorlar orqali baholash.
- **Xatolik xabarlarining avto-yo'qolishi**: Foydalanuvchi tugmachalarni bosa boshlashi bilan yuqoridagi xato qutisi avtomatik yo'qoladi.

### 👤 2. Foydalanuvchi Profili va Xavfsizlik
- **Random Avatarlar Tizimi (DiceBear Integration)**:
  - Yangi ro'yxatdan o'tgan yoki rasmi yo'q foydalanuvchilar uchun unikal, rang-barang 3D/Vektorli avatarlar avtomatik yaratiladi (`user.get_avatar_url`).
- **Ma'lumotlarni Himoyalangan (Qulflangan) Saqlash**:
  - Profil sahifasidagi ma'lumotlar standart holatda qulflangan (`disabled`) rejimda turadi.
  - ✏️ **"Tahrirlash"** tugmasi bosilganda maydonlar ochiladi va 💾 **"O'zgarishlarni saqlash"** tugmasi paydo bo'ladi.
- **Admin Paneldan Boshqariladigan Manzillar (Dropdown)**:
  - Manzil kiritish erkin matn emas, balki Admin Paneldan faollashtirilgan manzillar ro'yxatidan tanlanadi.
  - Test rejimida Farg'ona tumani hududlari (*Chimyon (Default)*, *Mindon*, *Xonqiz*) biriktirilgan.
- **Premium Custom Select Dropdown**:
  - Silliq animatsiyali, 220px skrolli, moslashtirilgan chevron va joylashuv belgisiga ega zamonaviy dropdown komponenti.

### 🛒 3. E-Commerce & Savat Tizimi
- **Katalog va Mahsulotlar**: Kategoriyalar bo'yicha filterlash, arzonlashtirilgan narxlar (Discount price), chegirma foizi va aksiyalar.
- **Jonli Qidiruv (Live Search)**: Nom bo'yicha mahsulotlarni real-vaqtda qidirish.
- **Savat va Istaklar ro'yxati (Cart & Wishlist)**: Mahsulotlarni savatga qo'shish, sonini o'zgartirish va istaklarga saqlash.
- **Buyurtmalar Tarixi**: O'tkazilgan buyurtmalar holatini (*Faol savat, Qabul qilindi, Yo'lda, Yetkazildi, Qaytarildi*) kuzatish.

---

## 🛠 Texnologiyalar Steki

| Qatlam | Texnologiya |
| :--- | :--- |
| **Backend** | Python 3.12, Django 5.x |
| **Frontend** | HTML5, Vanilla CSS3 (Custom Design System), JavaScript (ES6+) |
| **Ma'lumotlar bazasi** | SQLite3 / PostgreSQL tayyor |
| **Ikonkalar va Fontlar** | FontAwesome 5, LineAwesome, Google Fonts (Plus Jakarta Sans, Inter) |
| **Avatarlar Engine** | DiceBear Avatars API Integration |

---

## 🚀 Loyihani Mahalliy (Local) Ishga Tushirish

### 1. Repozitoriyani klonlash:
```bash
git clone https://github.com/Muxammadaziz-boss/Dpmarket.git
cd Dpmarket
```

### 2. Virtual muhitni yaratish va faollashtirish:
```bash
python -m venv .venv
# Windows uchun:
.\.venv\Scripts\activate
# Linux/macOS uchun:
source .venv/bin/activate
```

### 3. Kutubxonalarni o'rnatish:
```bash
pip install -r requirements.txt
```

### 4. Migratsiyalarni bajarish:
```bash
python manage.py migrate
```

### 5. Superuser (Super Admin) yaratish:
```bash
python manage.py createsuperuser
```

### 6. Dev-serverni ishga tushirish:
```bash
python manage.py runserver
```

Sayt brauzerda `http://127.0.0.1:8000/` manzili bo'yicha ochiladi. Admin panel esa `http://127.0.0.1:8000/admin/` manzilida joylashgan.

---

## 📁 Loyiha Strukturasi

```text
To'garak shop/
├── config/                  # Django proyekt sozlamalari (settings.py, urls.py)
├── main/                    # Asosiy ilova (models, views, urls, admin, tests)
│   ├── migrations/          # Ma'lumotlar bazasi migratsiyalari
│   ├── admin.py             # Admin panel sozlamalari (Address, User, Product va b.)
│   ├── models.py            # User, Product, Category, Cart, Address modellari
│   ├── urls.py              # URL yo'nalishlari va API endpoints
│   └── views.py             # Ko'rinishlar (Register, Login, Profile, APIs)
├── templates/               # HTML shablonlar
│   ├── base.html            # Asosiy karkas va menyu
│   ├── front/               # Foydalanuvchi sahifalari (login.html, profile.html va b.)
│   └── dashboard/           # Admin boshqaruv paneli shablonlari
├── static/                  # Statik fayllar (CSS, JS, Rasmlar, Shriftlar)
├── media/                   # Foydalanuvchilar yuklagan fayllar (Rasmlar, Avatarlar)
├── manage.py                # Django buyruqlar menejeri
└── README.md                # Dokumentatsiya
```

---

## 🧪 Testlarni Ishga Tushirish

Avtomatik integratsion va unit testlarni bajarish uchun quyidagi buyruqni bering:
```bash
python manage.py test main
```

---

© 2026 **DpMarket**. Barcha huquqlar himoyalangan.
