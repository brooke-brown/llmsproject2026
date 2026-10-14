# Direction Startup: Sunsama

Sunsama is a daily planning and productivity app that helps users organize tasks, prioritize work, and manage their schedules by integrating to-do lists with calendars.

## 1. Solves one problem extremely well

Sunsama helps users organize their daily tasks and manage their time effectively. Our reimagined version focuses specifically on automatically creating realistic daily schedules with AI. It eliminates the need to manually prioritize and time-block tasks.

## 2. Knowledge of its customers + access to them

Sunsama's existing customers are primarily busy professionals, including entrepreneurs, executives, engineers, product managers, and other knowledge workers managing competing priorities. Our reimagined version would expand to college students and early-career professionals who struggle with time management. As UVA students, we have firsthand experience with these challenges and direct access to potential users for interviews, beta testing, and feedback.

## 3. Familiarity with (or able to gain familiarity with) required infrastructure

This app requires a smartphone and knowledge of the domain, which is in the productivity space. We have experience owning a mobile app end-to-end so this is within the scope.

## 4. Able to reimagine how it can be much better

While Sunsama’s existing AI assistant, Sunny, helps users create tasks and plan their days, our version would proactively manage and continuously optimize their schedules. It would learn from actual task completion times, predict potential deadline conflicts, and automatically redistribute work before users fall behind. Rather than simply assisting with planning, our AI would act as an autonomous scheduling engine that anticipates and prevents time-management problems.

## 5. LLM / Generative AI is essential (not a sidebar chatbot)

The LLM would serve as the core decision-making engine, analyzing user behavior, task complexity, deadlines, and historical completion times to predict scheduling needs. It would proactively identify risks, break down complex tasks, and generate personalized scheduling adjustments before conflicts arise. Unlike Sunny's assistant-based approach, our system would use AI continuously to anticipate workload problems and autonomously replan schedules rather than waiting for user input.

## 6. No LLM-generated artifact reaches the user without passing through a validator

Every AI-generated schedule or adjustment would pass through an automated validator before reaching the user. The validator would check for calendar conflicts, missed deadlines, unrealistic time allocations, and violations of user preferences. Only schedules that satisfy these constraints would be displayed or applied to the user's calendar.

## 7. Validation errors automatically feed back into the LLM to self-correct

If a generated schedule fails validation due to conflicts, unrealistic time estimates, or missed deadlines, the validator would automatically send structured error messages back to the LLM. The LLM would use this feedback to revise the schedule and resolve the identified issues. This process would repeat until the schedule passes validation or the system determines that the constraints cannot be satisfied.

## 8. Deployable end-to-end (more than an MVP)

We would build a web app with user authentication, calendar integrations, AI-driven scheduling, automated validation, and continuous replanning. Users could connect their calendars, input tasks and deadlines, and receive personalized schedules that automatically adapt based on their progress. The application could be deployed using Vercel and with Google Cloud Platform.


