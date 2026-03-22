# DicomFlowRouter
DicomFlowRouter: A lightweight, Docker-native DICOM routing engine. Features a pynetdicom-based listener and an automated retry/sync worker managed by Ofelia scheduler. Designed with a modular "Building Block" philosophy for resilient medical data workflows. Currently in active development.
DICOM Flow Router
A lightweight, Python-based DICOM routing engine with high-availability features. It acts as an intelligent intermediary between DICOM modalities (Scanners) and PACS systems, ensuring data integrity even during network or destination failures.

🌟 Key Features
Intelligent Routing: Dynamic forwarding based on modular configuration files.

Failover Handling: Automatically detects if a destination is offline and queues images locally.

Robust Retry Logic: A dedicated scheduler service (Ofelia) manages the re-transmission of queued files once destinations are back online.

Dockerized Architecture: Fully containerized setup for easy deployment and isolation.

Production-Ready Logging: Detailed, synchronized logs for both the router and underlying DICOM communication.

📂 Project Structure
src/: The core logic. Contains the Router SCP and the Retry Sender SCU.

docker/: Infrastructure definitions (Dockerfile and Python dependencies).

examples/: Out-of-the-box demonstration environments.

01-SimpleSendwithRetry/: A complete sandbox with two Orthanc nodes (Sender/Receiver) to test the failover and recovery cycle.

🛠️ Getting Started (Example 01)
This example demonstrates a full cycle: Sender PACS → Router → Receiver PACS, Receiver PACS can be stopped to simulate failure.

Prerequisites
Docker and Docker Compose installed.

Installation & Launch
Clone the repository:

git clone https://github.com/DevByBandesz/DicomFlowRouter.git
cd DicomFlowRouter/examples/01-SimpleSendwithRetry

Start the environment:

docker-compose up -d --build

Testing the Routing
Sender UI: http://localhost:8041

Receiver UI: http://localhost:8042

The Workflow: Send a study from the Sender Node to ROUTER_AET (port 11112). It will be immediately forwarded to the Receiver Node.

Testing the Failover
Stop the Receiver Node: docker stop dcm_receiver_node

Send another study to the Router.

Check the logs: the Router will log a network error and save the file to the local Working directory.

Restart the Receiver: docker start dcm_receiver_node

Within 1 minute, the dcm_retry service will automatically detect the file and forward it.

🛠️ Getting Started (Example 02: Routing over VPN)
This example demonstrates a complex, multi-site DICOM network simulation. It features two isolated subnets connected via a VPN Gateway container, including a scheduled routing logic and an SOP Class whitelist.

The Infrastructure
Site-A (172.20.0.0/24): Internal LAN with a Sender PACS, a Local Archive, and the DICOM Router.

VPN Gateway: A privileged container acting as a firewall and router between sites.

Site-B (10.50.0.0/24): Remote LAN with a Destination PACS (Target).

Installation & Launch
Clone the repository and navigate to the example folder:


git clone https://github.com/DevByBandesz/DicomFlowRouter.git
cd DicomFlowRouter/examples/02-RoutingoverVPN

Start the environment:

docker-compose up -d --build

Testing the Workflow
Sender UI: http://localhost:8041

Local PACS UI: http://localhost:8042

Remote PACS UI (Site-B): http://localhost:8142

The Routing Logic:
Send a study from Sender PACS to ROUTER_AET (port 11112).

The Router immediately forwards a copy to the Local PACS.

The Router checks the Schedule for the Remote PACS.

Testing Scheduled Routing & Failover
By default, the example uses a Cyclic Test Schedule in router/remote/config.ini:
scheduled = m10 < 5 (Sends only during the first 5 minutes of every 10-minute block).

Inside Window (e.g., 20:02): Send a study. It will be forwarded to Site-B immediately.

Outside Window (e.g., 20:07): Send a study. The Router logs: Outside schedule... Saving to retry queue.

The Recovery: Wait until the next 10-minute block starts (e.g., 20:10). The dcm_scheduler service (powered by Ofelia) will trigger the retry_sender.py, which detects the open window and clears the queue.

⚙️ Configuration
The system uses a two-tier configuration approach:

Router Config (router/config.ini): Defines the global identity of the router (AE Title, ports, logging levels).

Endpoint Config (router/endpoint/config.ini): Defines where the images should be forwarded (Destination IP, Port, AE Title).

Note: You can add multiple endpoints by creating new subdirectories under the router/ folder, each with its own config.ini.

⚙ Configuration Details
Cyclic Scheduling (Testing Mode)
To make testing easier without waiting for nighttime, we use the m (minute) indicator:

m10 < 5: Active for 5 mins, then paused for 5 mins.

m60 < 30: Active for the first half of every hour.

Production Scheduling
For real-world scenarios, use standard time ranges:

scheduled = 19:00 - 07:00: Only sends at night.

scheduled = 08:00 - 12:00, 14:00 - 18:00: Split shifts.

scheduled = 18:00-22:00, 22:30-6:00 : Send at night with a pause.


SOP Class Whitelisting / Blacklisting
The router filters incoming and outgoing objects based on their SOP Class UID. 
This ensures only supported modalities (e.g., CT, MR, CR) are forwarded, preventing network clutter from unsupported DICOM objects like Structured Reports or Encapsulated PDFs.


⚖️ Legal & Fair Use Notice
⚠️ Medical Disclaimer
IMPORTANT: This software is not certified as a medical device (CE-marked or FDA-approved). It is provided strictly for educational and workflow-optimization purposes. The author(s) shall not be held liable for any data loss, delayed diagnosis, or clinical errors resulting from the use or misuse of this tool. Use in primary diagnostic workflows is at your own risk and requires independent validation.

🛡️ Data Privacy & Security Disclaimer
The user is exclusively responsible for the security of the data handled by this software.

Liability: The author(s) assume no responsibility for data leaks, unauthorized access to medical records (PHI), or violations of data privacy regulations (GDPR, HIPAA, etc.).

Storage: Be aware that the failover mechanism stores raw DICOM files in the local file system. It is the user's responsibility to secure these volumes.

🏛️ Professional & Clinical Deployment
To maintain the quality and track the impact of DicomFlowRouter, we request the following:

Commercial Use: Reach out for formal authorization if you bundle this into a commercial product.

Clinical Feedback: Notify the maintainer if deployed in production. Hearing about real-world use helps us prioritize stability.

By choosing to bypass these requests and using the software in professional settings without notification, you acknowledge that you are operating without the maintainer's direct guidance and take full responsibility for any integration or security issues.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.