ClipFlow AI
Autonomous Content Arbitrage Engine
Product Requirements Document  |  v1.0  |  February 20, 2026
Status	Final Draft — Ready for Implementation
Owner	Project Lead (In-House Build)
Version	1.0
Date	February 20, 2026
Target Revenue	$10,000+ MRR within 90 days of go-live
 
1. Vision & Problem Statement

1.1 Vision
To become the world's leading Autonomous Content Arbitrage Engine: a system allowing a single individual to manage a global empire of faceless social media channels (Theme Pages) across multiple languages and niches, generating passive income through high-velocity viral content and automated monetization (Whop / Affiliates).

⚠️ Strategic Note: The vision must include a transition path toward original IP creation. Building entirely on top of third-party content creates platform dependency and legal exposure. The system's long-term defensibility requires owning an audience, not just arbitraging one.

1.2 The Problem
The four core pain points this system is designed to eliminate:

•	Manually scanning YouTube for viral moments, downloading, clipping, reframing, and subtitling consumes 4–6 hours per video. A single person cannot scale to 10+ channels manually. Content Bottleneck:
•	Content has a short shelf life. If a clip is not published within hours of the source video going live, its viral potential drops 60–80%. Viral Decay:
•	Platforms penalize unoriginal content. Simple re-uploads are suppressed. Content requires deep visual transformation to pass algorithm originality filters. Originality Penalty:
•	Converting views to revenue requires consistent CTAs and link management — tasks routinely missed in manual workflows. Monetization Friction:

2. Target Users & Personas

2.1 Primary Persona — "The Arbitrage Mogul"
Attribute	Detail
Profile	Expert in digital trends and affiliate marketing
Goal	Run 20+ TikTok/Reels/Shorts accounts feeding traffic to high-ticket Whop communities
Strategy	Create faster, more engaging short-form versions of established influencer content (Huberman, Hormozi, etc.)
Success Metric	$10k+ MRR with <2 hours of manual oversight per week
Key Frustration	Spending 4–6 hours per clip that could go stale before it's published

3. Detailed Product Architecture

3.1 High-Level Architecture
The system is built as a series of Asynchronous Microservices to ensure a 3-hour 4K video download cannot crash the rest of the pipeline. Each service communicates via a job queue (Redis/SQS) and fails gracefully with retry logic.

Service	Codename	Responsibility
Sniffer	The Watcher	Monitor target YouTube channels via Google API + RSS polling
Downloader	Ingestion Engine	Pull high-quality source files via yt-dlp + residential proxies
Analyzer	Clinical Brain	Transcribe + score viral segments via Deepgram + GPT-4o-mini
Renderer	Visual Studio	Reframe, caption-burn, and grade clips via FFmpeg + GPU
Publisher	Distributor	Schedule and post clips via Ayrshare API

3.2 Data Model — Job Schema
🆕 Added: The original PRD had no data model. Every service must share a common Job object. Below is the canonical schema.

Each processing unit is a Job record stored in PostgreSQL, with status tracked through its lifecycle:

Field	Type	Description
job_id	UUID	Primary key, generated on Sniffer trigger
source_url	STRING	Original YouTube video URL
channel_id	FK	Reference to monitored channel configuration
status	ENUM	pending | downloading | analyzing | rendering | approval | scheduled | published | failed
viral_score	FLOAT	AI-assigned 1–10 score for segment virality
clip_manifest	JSONB	{ clip_start, clip_end, headline, visual_style, hook_score }
render_path	STRING	S3 path of the rendered .mp4 file
published_urls	JSON	Array of platform URLs after posting
error_log	TEXT	Last error message if status = failed
retry_count	INT	Number of retry attempts (max 3 per stage)
created_at / updated_at	TIMESTAMP	Full audit trail of job lifecycle

3.3 Error Handling & Retry Logic
🆕 Added: The original PRD had no failure handling. A pipeline without retry logic is fragile by design.

Every service stage must implement the following failure contract:

•	Max Retries: 3 attempts per stage with exponential backoff (5s, 30s, 5min).
•	Dead Letter Queue (DLQ): Jobs exceeding max retries move to a DLQ for manual inspection — never silently discarded.
•	Stage Isolation: A failure in the Renderer does not block new Sniffer or Downloader jobs. Each queue is independent.
•	Alerting: Failed jobs trigger a Slack/email alert with job_id, stage, and error_log content.
•	Idempotency: All stages must be safe to re-run on the same job_id without creating duplicate outputs.

Failure Scenario	Retry Strategy	Fallback Action
yt-dlp IP ban	Rotate proxy, retry after 30s	Flag channel as throttled for 1hr
Deepgram timeout	Retry x3, exponential backoff	Move to DLQ, alert owner
GPT-4o-mini rate limit	Queue delay + retry	Use cached scoring model
Ayrshare API error	Retry x3 with backoff	Save to scheduled queue, retry next window
RunPod GPU unavailable	Retry with alternate region	Alert + pause render queue
 
4. MVP Feature Specifications

4.1 Feature 1: Target Channel Sniffer
An automated monitoring system for YouTube that detects new uploads and filters by viral potential.

Requirements
•	Up to 50 concurrent target channels.
•	Polling frequency: every 10 minutes via Google Data API + RSS fallback.
•	Smart Filter: only process videos matching trigger thresholds:
◦	View Velocity: >10,000 views within first hour of publish.
◦	Minimum length: >10 minutes (short-form source material is rarely worth clipping).
◦	Optional: Keyword match in title/description (e.g., "stock picks", "mindset").
•	Channel configuration stored in PostgreSQL — no Google Sheets dependency in production.

Milestone
A terminal script accepting a channel URL that returns a JSON array of triggered video IDs within 10 minutes of publish.

4.2 Feature 2: AI Viral Intelligence (The Brain)
Converts raw transcripts into a JSON manifest of high-value, time-stamped clip candidates.

Analysis Logic
•	Word-Level Timestamping via Deepgram Nova-2 — every word carries a start/end time for frame-accurate captioning.
•	Viral Scoring (1–10) per 30–60 second segment based on: Retainability, Emotional Peak, Controversy Level, and Actionability.
•	Hook Detection: forced extraction and scoring of the first 3 seconds of each candidate clip.
•	Keyword Triggers: user-configurable topic filters applied after transcription ("stock picks", "money", "failure", etc.).

Output Format
A clip_manifest JSON object per candidate:
•	clip_start / clip_end (seconds, float precision)
•	headline (AI-generated suggested caption overlay)
•	viral_score (float 1–10)
•	hook_score (float 1–10 for the first 3 seconds)
•	visual_style_preference ("energetic" | "calm" | "controversial")
•	keyword_triggers (array of matched keywords)

Milestone
A terminal script that accepts a YouTube URL and returns 5 ranked clip candidates as JSON within 2 minutes of video publish.

4.3 Feature 3: Approval Dashboard
🆕 Moved to Phase 1: The original PRD deferred the dashboard to Week 9. This is incorrect. Clips will be rendered from Week 5 onward and need a review interface immediately. The dashboard must be ready before the renderer.

A minimal web UI (React + FastAPI) for clip review before publishing.

Requirements
•	Display rendered clip inline (HTML5 video player).
•	Show: viral_score, hook_score, suggested headline, matched keyword_triggers.
•	One-click actions: Approve, Reject, Edit Headline, Edit Schedule Time.
•	Bulk approve mode for high-confidence clips (viral_score > 8.5).
•	Audit log: every approval/rejection recorded with timestamp.

Early Metrics (Available Before Week 9)
•	Approval Rate: % of AI-selected clips approved by the operator. Target: >70%.
•	Rejection Reason Tags: operator tags why a clip was rejected (off-brand, audio issue, wrong hook, etc.) — feeds back into scoring model.

4.4 Feature 4: Automated Visual Processing (The Studio)
Auto-Reframing
•	Identify speaker face using MediaPipe face detection.
•	Apply Smooth Follow logic via FFmpeg crop filter — camera tracks speaker movement without jarring cuts.
•	Fallback for multi-speaker frames: center crop with dynamic padding.

Dynamic Captions (Hormozi Style)
•	High-contrast color scheme: Yellow / White / Green on dark stroke.
•	Auto-emoji injection on keyword match (configurable dictionary).
•	Pulse/highlight effect on the active word.
•	Caption style configurable per channel profile — different channels can have different aesthetics.

Visual Transformation (Shadowban Mitigation)
•	Apply subtle film grain (intensity: 3–5%) and color grade per clip — unique digital fingerprint.
•	Randomize posting time within a ±45 minute window of the scheduled slot to break pattern detection.
•	Vary caption color scheme slightly per channel to differentiate fingerprints.

Background Music
•	Auto-selection of royalty-free audio matched to clip sentiment.
•	Music source: Pixabay / YouTube Audio Library API (not scraped — licensed endpoints only).
•	Volume ducking: music auto-attenuates under speech, rises during pauses.

Milestone
A folder of 10 ready-to-post vertical clips with burned-in captions, auto-reframed, color-graded, and scored — produced without manual editing.

4.5 Feature 5: Automated Monetization (The Money Layer)
•	Auto-comment on TikTok with Whop affiliate link (posted 10–30 minutes after clip to avoid spam detection).
•	Burned-in 2-second end screen: "Join the community — Link in Bio."
•	Bio link auto-updated via Ayrshare API on post — single source of truth for all platform links.
•	UTM parameters auto-appended to every affiliate link for revenue attribution per channel/clip.

 
5. Account Operations & Platform Compliance

🆕 Added: The original PRD did not address account warming or platform fingerprinting — the two most common causes of account bans in automated content operations.

5.1 Account Warming Protocol
New accounts cannot immediately begin posting 3+ videos per day. Platforms detect this pattern and restrict reach or ban accounts. The following warming schedule is mandatory for every new account:

Week	Posts/Day	Content Type	Manual Actions Required
1	1	High-quality clip only	Follow 10 accounts in niche, like 20 posts/day
2	2	Clips + 1 trending audio	Comment on 5 posts/day in niche
3	3	Full automated cadence	Monitor for restrict flags
4+	3–5	Full automated cadence	Minimal — system handles

5.2 Platform Fingerprinting Mitigation
TikTok and Instagram use ML models to detect coordinated inauthentic behavior across accounts — not just content similarity. The following practices must be enforced at the infrastructure level:

•	Device Isolation: Each account must operate from a unique device profile (via antidetect browser or dedicated device). Never manage 2+ accounts from the same device ID.
•	IP Isolation: Each account must have a dedicated residential proxy IP. Rotating shared proxies are insufficient — platforms have learned to identify shared proxy pools.
•	Posting Pattern Variance: Randomize posting times within ±45 minutes of the scheduled time. Never post at exact daily intervals.
•	Engagement Simulation: Each account should have human-like browsing behavior between posts — scroll feed, watch videos, leave organic comments. Consider a lightweight engagement bot per account on a delayed schedule.

5.3 Third-Party Dependency Risk
🆕 Added: The system's critical path runs through Ayrshare. A single vendor shutdown or policy change could halt all publishing. Mitigation is required.

Dependency	Risk	Mitigation
Ayrshare	Pricing spike, policy ban on automation	Abstract publisher behind interface; build direct TikTok API fallback
RunPod	GPU unavailability in region	Configure fallback to Lambda Labs or Modal
Deepgram	Outage / price change	Abstract transcription layer; AssemblyAI as drop-in fallback
SmartProxy	IP pool detection by platforms	Evaluate dedicated residential IPs per account
YouTube Data API	Quota limits, API deprecation	RSS polling as primary; API for metadata enrichment only
 
6. Technology & Integration Specifications

6.1 Third-Party Integration Stack
Tool	Function	Integration Method	Est. Cost/Video
Deepgram Nova-2	Word-level Transcription	REST API	~$0.12
GPT-4o-mini	Viral Analysis + Scoring	REST API	~$0.02
RunPod / Lambda	GPU Rendering (A100)	Serverless Container	~$0.30
Ayrshare	Social Posting	SDK/API	~$0.10
SmartProxy	Residential Proxies	HTTP Proxy	~$0.05
S3 Storage	Temp file storage (24hr TTL)	AWS SDK	~$0.08
PostgreSQL (RDS)	Job state + audit log	ORM / Direct	~$0.03
Redis / SQS	Job queue	SDK	~$0.02
	REALISTIC TOTAL	(incl. ~20% failure retry overhead)	~$1.20–1.50

⚠️ Cost Note: The original PRD estimated $0.59/video. This excluded S3 bandwidth, database costs, job queue infrastructure, and the ~20% retry overhead from expected failures. The realistic figure is $1.20–1.50/video. Budget planning should use the higher figure.

6.2 Storage & Bandwidth Optimization
•	Temporary Storage: S3 with a 24-hour Lifecycle Policy. Original 4K source files are deleted immediately after 9:16 clips are rendered.
•	Rendered Clips: Stored in S3 for 7 days post-publish (required for analytics retrieval), then deleted.
•	Manifests & Metadata: Stored in PostgreSQL indefinitely for analytics and retraining the scoring model.

6.3 Infrastructure Architecture
All services run as containerized Python microservices on Docker/ECS. Communication is event-driven via Redis queues. The system must be horizontally scalable — adding more channels requires only increasing queue workers, not re-architecting.
•	Sniffer: Lightweight cron-based service, 1 instance per 50 channels.
•	Downloader: 3 concurrent workers max (proxy rate limit compliance).
•	Brain (Analyzer): Stateless, scales to N instances as queue depth grows.
•	Renderer: GPU-bound, launches serverless RunPod containers on demand.
•	Publisher: Rate-limited to platform API quotas, single-threaded per account.
 
7. Detailed User Stories

ID	Role	Story	Acceptance Criteria
US-1	Operator	I want to add 5 podcast channels to my Finance niche so the system watches them 24/7.	Channels appear in dashboard, first poll completes within 10 min, new video triggers Sniffer alert.
US-2	Operator	I want the AI to detect moments where a guest discusses specific stock picks because those go viral.	User can define keyword triggers per channel; only clips containing triggers are surfaced.
US-3	Operator	I want the system to auto-center the speaker and add captions without opening CapCut.	Rendered clip passes human review: speaker is centered >90% of the time, captions sync to within 200ms.
US-4	Operator	I want to review a clip and approve/reject it in under 10 seconds.	Dashboard shows clip, score, and headline. One-click Approve sends to scheduling queue.
US-5	Operator	I want the same clip posted to 3 platforms simultaneously on a set schedule.	Ayrshare integration distributes to TikTok, Instagram Reels, and YouTube Shorts in one action.
US-6	Operator	I want to know which clips drove affiliate revenue so I can create more like them.	Each clip has a unique UTM link. Revenue dashboard shows clicks and conversions per clip_id.
US-7	Operator	I want to be alerted when a job fails so I can investigate without the pipeline silently breaking.	Slack/email alert on DLQ entry includes job_id, failed stage, and error summary.
 
8. Implementation Roadmap (14 Weeks)

🆕 Extended: The original 12-week plan was revised to 14 weeks. Week 5 was freed up to build the Approval Dashboard before the renderer goes live — a critical sequencing fix.

Phase 1: Ingestion & Intelligence (Weeks 1–4)
1.	Build yt-dlp downloader with proxy rotation and retry logic.
2.	Integrate Deepgram Nova-2 for word-level transcription.
3.	Implement GPT-4o-mini Viral Scoring with keyword trigger support.
4.	Stand up PostgreSQL schema and Redis job queue.

Milestone: Terminal script accepts a YouTube URL and returns 5 ranked JSON clip candidates within 2 minutes of video publish. All jobs visible in DB with status tracking.

Phase 2: Approval Dashboard (Week 5)
🆕 Phase Added: No clips should be rendered until there is a UI to review them. Building the renderer first creates a backlog with no management interface.

5.	Build React + FastAPI approval dashboard.
6.	Implement approve / reject / edit headline flows.
7.	Implement audit log and rejection tagging.

Milestone: Operator can review, approve, and reject clips from the browser. Rejected clips are tagged with reason codes visible for future model tuning.

Phase 3: Render Engine (Weeks 6–9)
8.	Develop FFmpeg 9:16 crop pipeline with MediaPipe face tracking.
9.	Build Caption Burner using .ass subtitle files with pulse effect.
10.	Implement film grain + color grade for fingerprint differentiation.
11.	Integrate background music selection and volume ducking.
12.	Connect renderer output to approval dashboard.

Milestone: 10 ready-to-post vertical clips produced without manual editing, visible in dashboard for one-click approval.

Phase 4: Distribution & Automation (Weeks 10–12)
13.	Integrate Ayrshare for multi-platform scheduling.
14.	Implement UTM link generation per clip.
15.	Build affiliate comment auto-posting with delay logic.
16.	Implement posting time randomization (±45 min variance).

Milestone: First 100% automated post — from YouTube publish to TikTok/Reels/Shorts — without manual intervention beyond one dashboard approval click.

Phase 5: Hardening & Scale (Weeks 13–14)
17.	Implement full DLQ alerting and retry escalation.
18.	Load test pipeline to 50 concurrent channels.
19.	Build account warming scheduler.
20.	Set up revenue attribution dashboard (UTM → Whop conversion tracking).

Milestone: System operates at full capacity with <5% job failure rate and zero-touch operation for >95% of videos.
 
9. Success Metrics

🆕 Revised: The original metrics were output-focused (followers, revenue) without pipeline health metrics. Early-stage metrics must validate the system is working before optimizing for growth.

9.1 Pipeline Health Metrics (Weeks 1–8)
Metric	Target	Why It Matters
AI Approval Rate	>70%	Validates scoring model is surfacing quality clips
Job Failure Rate	<5%	Confirms pipeline stability and retry logic is working
Time-to-Render (avg)	<45 min from publish	Measures viral window exploitation speed
Sniffer Latency	<15 min from video publish	Confirms monitoring is real-time enough
Cost per Processed Video	<$1.50	Validates unit economics before scaling

9.2 Growth Metrics (Weeks 9–14)
Metric	Target	Why It Matters
Zero-Touch Ratio	>95%	Operator spends <2hrs/week on oversight
Follower Growth (new account)	10,000 in 30 days	Validates content + distribution effectiveness
Engagement Rate	>5% (vs niche avg ~2–3%)	Confirms transformed clips outperform raw reposts
Affiliate CTR	>1.5% of views	Measures monetization conversion efficiency
MRR Target	$10,000+	Primary business success metric
 
10. Open Questions & Decisions Required

🆕 Added: PRDs should document what is NOT yet decided. Unresolved questions become hidden blockers.

#	Question	Owner	Decision Deadline
1	Will Ayrshare be abstracted behind an interface from day one, or is direct integration acceptable for MVP?	Project Lead	Week 1
2	What is the account warm-up plan for the first 3 accounts launched? Will manual activity be performed by the operator or simulated?	Project Lead	Week 1
3	What is the trigger threshold for the Viral Score auto-approve path (bypass manual review)?	Project Lead	Week 5
4	Which Whop community is the initial monetization destination? Are multiple affiliate programs needed?	Project Lead	Week 8
5	At what MRR level does the system justify hiring a VA for the remaining 5% manual oversight?	Project Lead	Week 12
6	What is the 18-month plan for original content creation to reduce platform dependency?	Project Lead	Week 12



ClipFlow AI — PRD v1.0  |  February 20, 2026  |  CONFIDENTIAL
