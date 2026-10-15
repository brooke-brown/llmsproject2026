# Direction Startup: When2Meet / MeetingBird

When2meet and Meetingbird are scheduling platforms that help users coordinate meetings by identifying mutually available times and reducing scheduling conflicts.

## 1. Solves one problem extremely well

When2meet and Meetingbird help users find overlapping availability to schedule meetings. Our reimagined version would focus on automatically identifying the most optimal meeting time based on participants' availability, preferences, and priorities. It would eliminate the need to compare schedules after logging in/many account registration steps or negotiate meeting times. Our optimization keeps the simplicity of when2meet but adds in the AI assistance from Meetingbird.

## 2. Knowledge of its customers + access to them

Meetingbird's target customers include professionals, businesses, and teams that frequently schedule meetings, while When2meet is popular for group coordination among students and organizations. Our reimagined version would initially target college students, student organizations, and project teams struggling to coordinate group meetings. As UVA students, we have firsthand experience with scheduling challenges and direct access to potential users for interviews, beta testing, and feedback.

## 3. Familiarity with (or able to gain familiarity with) required infrastructure

This app requires a smartphone and knowledge of the domain, which is in the productivity space. We have experience owning a mobile app end-to-end so this is within the scope.

## 4. Able to reimagine how it can be much better

While When2meet identifies overlapping availability and Meetingbird streamlines calendar-based scheduling, our version would proactively negotiate the best meeting time across multiple participants. It would interpret natural-language constraints, account for meeting urgency and individual preferences, and recommend compromises when no ideal time exists. Rather than simply identifying open time slots, our AI would act as an intelligent scheduling coordinator that balances competing priorities. Also, we would simplify the app rather than having MeetingBird have so much of a barrier (login, tracking, 9000 features, etc).

## 5. LLM / Generative AI is essential (not a sidebar chatbot)

The LLM would serve as the core reasoning engine, interpreting participants' natural-language availability, preferences, and scheduling constraints. It would analyze competing priorities, propose scheduling compromises, and generate personalized recommendations when no mutually convenient time exists. Unlike traditional scheduling tools, our system would use AI to actively negotiate complex scheduling trade-offs rather than simply match available time slots, but without the extra steps/features that are extraneous.

## 6. No LLM-generated artifact reaches the user without passing through a validator

Every AI-generated meeting recommendation would pass through an automated validator before reaching users. The validator would check for calendar conflicts, time zone accuracy, meeting duration, participant availability, and violations of scheduling constraints. Only validated meeting times and recommendations would be displayed or booked.

## 7. Validation errors automatically feed back into the LLM to self-correct

If a proposed meeting time fails validation due to calendar conflicts, unavailable participants, or scheduling restrictions, the validator would automatically return structured error messages to the LLM. The LLM would revise its recommendations and propose alternative meeting times based on the feedback. This process would repeat until a valid option is found or the system determines that no feasible meeting time exists.

## 8. Deployable end-to-end (more than an MVP)

We would build a web application with user authentication, calendar integrations, AI-powered meeting coordination, automated validation, and meeting invitations. Users could connect their calendars, enter scheduling preferences, invite participants, and receive optimized meeting recommendations that can be directly added to their calendars. The application could be deployed using Vercel for the frontend and Google Cloud Platform for the backend and database infrastructure.
