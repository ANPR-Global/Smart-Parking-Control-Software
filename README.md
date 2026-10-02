# 🚗 Smart Parking Control Software

A high-performance, hybrid Automatic Number Plate Recognition (ANPR) system combining low-latency local detection (Python & C++) with high-accuracy cloud OCR pipelines. Designed for automated parking barriers, toll gates, and compound surveillance systems.

🔗 **Website:** [wyom.in](https://wyom.in) | 🌐 **Dashboard:** [wyom.in/parking](https://wyom.in/parking) | 🏷️ **Pricing:** [wyom.in/#pricing](https://www.google.com/search?q=https://wyom.in/%23pricing)

---

## 📌 Features

* **Hybrid Architecture:** Ultra-fast local vehicle/plate detection via C++ background workers paired with cloud OCR accuracy.
* **Real-Time Web Dashboard:** Accessible via any browser to manage gates, view live logs, and control barrier access remotely.
* **Access Control & Whitelisting:** Map vehicles to tenants, assign permitted parking areas, and manage gate access rights.
* **ROI Selection:** Interactive visual ROI (Region of Interest) selector to hone in on camera detection areas.
* **Resource Efficient:** Low CPU/RAM footprint—close the Python GUI and let optimized C++ binaries handle video streams silently.
* **Log Retention:** Cloud logging up to 40 days standard, extendable upon request.

---

## 💳 Pricing Tiers

Every account includes **100 FREE scans** per month. Upgrade to paid tiers as your parking capacity grows:

| Plan / Tier | Monthly Scans | Price (USD/mo) | Target Parking Size | Key Features |
| --- | --- | --- | --- | --- |
| **Free Tier** | 100 | $0.00 | Testing / Demo | Full dashboard & API access |
| **7,500 Scans** | 7,500 | $8.17 | ~25 spaces | Full ANPR pipeline |
| **15,000 Scans** ⭐ | 15,000 | $16.35 | ~50 spaces | Full ANPR pipeline (Popular) |
| **22,500 Scans** | 22,500 | $24.52 | ~75 spaces | Full ANPR pipeline |
| **30,000 Scans** | 30,000 | $32.70 | ~100 spaces | Full ANPR pipeline |
| **Custom Volume** | > 30,000 | Custom | > 100 spaces | Volume discounts, custom limits, 1-hr support contact |

---

## 🚀 Quick Start Guide

### 1. Local Application Setup

1. **Download/Clone Repository:**
```bash
git clone https://github.com/your-repo/Smart-Parking-Control-Software.git
cd Smart-Parking-Control-Software

```


2. **Build C++ Binaries:**
Compile the C++ core files using your platform's toolchain (e.g., GCC, MSVC, or Clang). This generates two executable binaries required for background stream processing.
3. **Run the Controller:**
```bash
python Local_APP_qt.py

```


*The compiled `.exe` background workers will start automatically alongside the Qt application.*

### 2. Cloud Activation

To link your local environment to the cloud pipeline and unlock allowance tiers:

1. Register at [wyom.in/parking](https://wyom.in/parking) and verify your email via OTP.
2. Navigate to the **Settings** tab on the web dashboard.
3. Generate your **API Key** and **Session Key**.
4. Copy and paste both keys into the running Python application settings screen and click **Save**.

### 3. Web Dashboard Configuration

1. **Parkings & Gates:** Go to the **Parkings** tab to add new parking site details and define entry/exit gates per parking lot.
2. **Tenants:** Go to the **Tenants** tab to register tenant profiles (vehicle owners).
3. **Vehicles:** Go to the **Vehicles** tab to map license plate numbers to tenants and set permitted parkings.
4. **Sync Local App:** Click **Save / Sync** inside the local Python App to pull cloud records.

### 4. Camera & Gate Hardware Setup (Python<img width="1915" height="915" alt="Dashboard" src="https://github.com/user-attachments/assets/0d751901-6304-4577-8902-9628f46aabb9" />
)

| Device Type | Supported Protocols |
| --- | --- |
| **IP Cameras** | IP / RTSP streaming URLs |
| **Barrier Gates** | IP / TCP trigger endpoints |

1. Input the RTSP Camera Stream URL and Barrier Gate TCP URL into the gate settings.
2. Click **Save** and then **Capture** to pull a live preview frame from the feed.
3. Click **Draw ROI**: Draw a bounding box on the image canvas where vehicle plates pass through.
4. Click **Save**.
5. Click **Save** again to refresh and update the C++ detection routines.

> 💡 **Performance Tip:** Close the main Python GUI once configured. C++ background processes will handle low-level detection with minimal CPU overhead.

---

## 🔒 Security & Data Retention

* **Database Logs:** Scan records and plate crops are securely stored for 40 days by default.
* **Extended Storage:** Need longer retention (60, 90, 365, or 730 days) for compliance? Contact [info@wyom.in](https://www.google.com/search?q=mailto%3Ainfo%40wyom.in) from your verified account email.

---

## 📩 Support & Roadmap

* **Website:** [wyom.in](https://wyom.in)
* **Contact / Inquiries:** [info@wyom.in](https://www.google.com/search?q=mailto%3Ainfo%40wyom.in)
* **Roadmap:** Additional AI features and integrations scheduled through Q4 2026. Contributions welcome!
