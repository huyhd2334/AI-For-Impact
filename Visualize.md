# SYSTEM ARCHITECTURE & WORKFLOWS

---

## 1. HIGH-LEVEL ARCHITECTURE

```mermaid
flowchart TD
    subgraph Clients ["1. CLIENTS"]
        A1["Citizen Mobile App"]
        A2["Dispatcher Web Portal"]
    end

    subgraph Gateway ["2. API GATEWAY"]
        B["Nginx Gateway"]
    end

    subgraph Services ["3. BACKEND SERVICES"]
        C1["Map Service"]
        C2["Routing Service (OSRM)"]
        C3["SOS Emergency Service"]
        C4["Incident Ticket Service"]
    end

    subgraph Storage ["4. DATA LAYER"]
        D1["PostgreSQL / PostGIS"]
        D2["Redis Cache"]
        D3["S3 / MinIO Storage"]
    end

    A1 --> B
    A2 --> B
    B --> C1
    B --> C2
    B --> C3
    B --> C4
    C1 --> D1
    C2 --> D1
    C3 --> D1
    C3 --> D2
    C4 --> D1
    C4 --> D3
```

---

## 2. FLOW 1: FLOOD & TRAFFIC MAP VISUALIZATION

```mermaid
flowchart LR
    A["1. User opens app"] --> B["2. App sends viewport bounding box"]
    B --> C["3. PostGIS queries flood & congestion data"]
    C --> D["4. Render layers on map"]
```

---

## 3. FLOW 2: SMART ROUTING (AVOID FLOOD & TRAFFIC)

```mermaid
flowchart TD
    A["1. Enter origin and destination"] --> B["2. Fetch active floods & congestion"]
    B --> C{"3. Apply routing penalty"}
    C -->|Flood > 30cm| D["Block road (Weight = Infinity)"]
    C -->|Congestion| E["Increase travel time cost"]
    D --> F["4. OSRM calculates optimal detour"]
    E --> F
    F --> G["5. Return safe route to user"]
```

---

## 4. FLOW 3: SOS EMERGENCY DISPATCH

```mermaid
flowchart TD
    A["1. Citizen triggers SOS (GPS + Media)"] --> B["2. PostGIS finds nearest emergency unit"]
    B --> C["3. WebSocket pushes real-time alert"]
    C --> D["4. Emergency unit accepts dispatch"]
    D --> E["5. Notify citizen & start navigation"]
```

---

## 5. FLOW 4: INCIDENT TICKETING (JAM & ACCIDENT)

```mermaid
flowchart TD
    A["1. Citizen reports incident"] --> B{"2. Check within 100m radius"}
    B -->|Existing incident| C["Upvote report (Deduplicate)"]
    B -->|New incident| D["Create new ticket"]
    D --> E["3. Dispatch traffic officer"]
    E --> F["4. Clear incident & update map"]
```
