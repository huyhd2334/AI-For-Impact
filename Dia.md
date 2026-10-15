    [ User (Mobile App) ]            [ Điều phối - Cứu hộ (Web/Mobile) ]
            │                                         │
            ▼                                         ▼
   ┌─────────────────────────────────────────────────────────┐
   │             API Gateway & Reverse Proxy                 │
   │                      (Nginx)                            │
   │             - Rate Limiting, JWT Auth,                  │
   └────────────┬─────────────────────────────┬──────────────┘
                │ HTTP / REST                 │ WebSocket Real-time
                ▼                             ▼
   ┌─────────────────────────────┐   ┌─────────────────────────────┐
   │       Core API Services     │   │     Realtime Socket Hub     │
   │  - Auth & User Service      │   │  - SOS Instant Alert Push   │
   │  - Map & Incident Service   │   │  - Traffic Density Stream   │
   │  - Ticket & Workflow Service│   │  - Responder GPS Tracker    │
   └────────────┬────────────────┘   └──────────────┬──────────────┘
                │                                   │
      ┌─────────┴──────────────┬────────────────────┴───────┐
      │                        │                            │ 
      ▼                        ▼                            ▼
┌──────────────────┐  ┌──────────────────┐       ┌──────────────────┐
│  Routing Service │  │  Message Queue   │       │   Redis Cache    │
│  (OSRM / OSM +   │  │ (Kafka/RabbitMQ) │       │  - Active GPS    │
│  Dynamic Penalty)│  │ - SOS Event Bus  │       │  - Session / Rate│
└────────┬─────────┘  │ - Ticket Events  │       └──────────────────┘
         │            └────────┬─────────┘
         │                     ▼
         │            ┌──────────────────┐
         │            │ Workers & Tasks  │
         │            │ - Push Noti (FCM)│
         │            │ - Auto Assign    │
         │            │ - Deduplication  │
         │            └────────┬─────────┘
         ▼                     ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Data Persistence Layer                      │
│ - PostgreSQL + PostGIS (Geometries, SOS, Tickets, Users)         │
│ - S3 / MinIO (Hình ảnh hiện trường tai nạn, ngập) / wasabi       │
└──────────────────────────────────────────────────────────────────┘
