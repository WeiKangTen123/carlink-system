# Carlink Studio 2.0 — Case-Driven Loss Adjuster Prototype

This prototype features a **Case-Driven Architecture** where each incident case has its own unique photo gallery, AI Vision bounding box annotations, vehicle blueprint hotspots, damaged component checklist, and repair calculations.

---

### 🚗 Featured Real Survey Case: `SLK 3063 Z` (Honda Vezel)

The prototype integrates the real-world survey photos from [`/SLK 3063 Z`](file:///Users/weikangten/Desktop/carlink-system-master/SLK%203063%20Z/):

- **Vehicle**: Honda Vezel 1.5 Hybrid (Registration: `SLK 3063 Z`)
- **Accident Type**: Rear-Left Corner Collision
- **Evidence Photos**: High-resolution photos from the case (`P1273082.JPG`, `P1273083.JPG`, `P1273087.JPG`, `P1273090.JPG`, etc.)
- **AI Vision Annotations**:
  - `[⚡ Tailgate Crease // 98.2%]`
  - `[⚡ Bumper Scuff & Crush // 96.5%]`
  - `[⚡ Sheetmetal Tear // 98.9%]`
  - `[⚡ Arch Misalignment // 94.0%]`
  - `[⚡ Gap Distortion > 8mm // 96.1%]`
- **Interactive Blueprint**: Hotspots dynamically reposition to the **Rear-Left** of the vehicle schematic.
- **Photo Category Filtering**: Filter by `All Photos (6)`, `Rear Impact (2)`, `Macro Close-up (2)`, and `Side / Quarter (2)`.

---

### 🔄 Multi-Case Switching

Switch cases at any time using:
1. **The Navbar Case Dropdown**: Select `SLK 3063 Z (Honda Vezel)`, `VAY 4821 (Honda Civic RS)`, or `WX 8888 A (Toyota Hilux)`.
2. **The Command Center Overview Table**: Click any row to automatically load that case and its photos into the Studio.

---

### 🎨 Dual Theme (Dark & White)

Toggle between **🌙 Dark Theme** and **☀️ White Theme** using the switch in the top navigation bar.

---

### 🚀 How to View the Prototype

- Open [`prototype/index.html`](file:///Users/weikangten/Desktop/carlink-system-master/prototype/index.html) in your browser.
- Or run a local server:
  ```bash
  python3 -m http.server 3333 --directory prototype
  ```
  Then visit `http://localhost:3333` in your browser.
