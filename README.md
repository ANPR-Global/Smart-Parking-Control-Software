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
<img width="1672" height="872" alt="Python" src="https://github.com/user-attachments/assets/7b182194-16cd-4c71-ba4c-d71333aafb50" />

<img width="1780" height="1162" alt="Local C++ Controller" src="https://github.com/user-attachments/assets/66c7f4a8-73c6-4ec9-bea4-afa00b81acb5" />



*The compiled `.exe` background workers will start automatically alongside the Qt application.*

### 2. Web Dashboard Configuration:

To link your local environment to the cloud pipeline and unlock allowance tiers:

1. Signup at [wyom.in/parking](https://wyom.in/parking) and verify your email via OTP.
   <img width="1055" height="807" alt="Sign-up" src="https://github.com/user-attachments/assets/b732e629-ba2c-482a-9d8c-6981fb85c2e4" />
   <img width="1052" height="805" alt="OTP" src="https://github.com/user-attachments/assets/af231e7f-7965-4a42-b2b5-078c198576f5" />
2. The **Dashboard** will load
   <img width="1915" height="915" alt="Dashboard" src="https://github.com/user-attachments/assets/c4c5418c-5592-4b9e-975b-0feb90ddd718" />
3. **Parkings & Gates:** Go to the **Parkings** tab to add new parking site details and define entry/exit gates per parking lot.
   <img width="1917" height="427" alt="Add Parking" src="https://github.com/user-attachments/assets/b5c319d1-2cd2-4d9d-bf4f-2ebc409ea0dc" />

4. **Tenants:** Go to the **Tenants** tab to register tenant profiles (vehicle owners).
5. **Vehicles:** Go to the **Vehicles** tab to map license plate numbers to tenants and set permitted parkings.
6. Navigate to the **Settings** tab on the web dashboard.
7. Generate your **API Key**.

   <img width="590" height="290" alt="Gen API" src="https://github.com/user-attachments/assets/568af3ed-1f60-426e-b980-f3ad532d7f60" />
8. (**Session Key**) can be found in the developer console on your browser (Ctrl + Shift + I) > Application> Storage > Local Storage > wyom.in > Client Session Token -> copy the value
9. Copy and paste both keys into the running Python application settings screen and click **Save** for each.
   <img width="2615" height="1500" alt="Save keys" src="https://github.com/user-attachments/assets/a4031b3f-5127-4d87-a562-7fd255f9ea04" />
   
10. **Sync Local App:** Now the Python app will pull saved cloud records.
   <img width="2577" height="552" alt="Activated" src="https://github.com/user-attachments/assets/434ce6ab-3abe-4a30-8175-448675f4506a" />


---

### 4. Camera & Gate Hardware Setup (Python)

| Device Type | Supported Protocols |
| --- | --- |
| **IP Cameras** | IP / RTSP streaming URLs |
| **Barrier Gates** | IP / TCP trigger endpoints |

1. Select and lock the parkings you want to configure and control on that device (premise/location)
   <img width="2617" height="1505" alt="Lock parkings" src="https://github.com/user-attachments/assets/e1dcc0b0-a227-4c45-8caa-64f338421124" />
2. This will display the gates of the selected parkings.
   <img width="2870" height="1662" alt="Load Parkings" src="https://github.com/user-attachments/assets/e7bfe090-19ae-49ea-bd28-6f04e337b38e" />

3. Expand the Gate you want to configure
4. Input the RTSP Camera Stream URL and Barrier Gate TCP URL into the gate settings.
5. Click **Save** and then **Capture** to pull a live preview frame from the feed.
6. Click **Draw ROI**: Draw a bounding box on the image canvas where vehicle plates pass through, and save.
   <img width="1115" height="410" alt="ROI" src="https://github.com/user-attachments/assets/7087775e-f087-4495-bebc-8cb5ca6e2aaa" />
7. Click **Save** again to refresh and update the C++ detection routines.
   <img width="1277" height="845" alt="Gate Settings" src="https://github.com/user-attachments/assets/87215f46-0502-43ea-ba2a-794e01cefd3e" />

**Live Scans** are reflected on the dashboard. You can go to the **Parking Log** tab to see the permitted (*success*) and failed scans (*non-permitted vehicle, cloud failure*)
You can click on the **image icon** on each log item to view the image(plate) of that scan.
<img width="1912" height="640" alt="Parking Log" src="https://github.com/user-attachments/assets/25805939-c197-4db1-83f2-af92ce8dd8e1" />
<img width="1910" height="915" alt="Scan Image" src="https://github.com/user-attachments/assets/e9794428-2409-4e2a-bb4b-ae941ee6d4d8" />


> 💡 **Performance Tip:** Close the main Python GUI once configured. C++ background processes will handle low-level detection with minimal CPU overhead.
> Try not to preview multiple feeds at once. This may slow down your device due to video decoding.

---

## 🔒 Security & Data Retention

* **Database Logs:** Scan records and plate crops are securely stored for 40 days by default.
* **Extended Storage:** Need longer retention (60, 90, 365, or 730 days) for compliance? Contact [info@wyom.in](https://www.google.com/search?q=mailto%3Ainfo%40wyom.in) from your verified account email.

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


## 📩 Support & Roadmap

* **Website:** [wyom.in](https://wyom.in)
* **Contact / Inquiries:** [info@wyom.in](https://www.google.com/search?q=mailto%3Ainfo%40wyom.in)
* **Roadmap:** Additional AI features and integrations scheduled through Q4 2026. Contributions welcome!
