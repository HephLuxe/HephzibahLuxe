# Hephzibah Luxe Model Relationship Map

```
User (accounts)
  │
  ├──1:1──► ClientPortal (portal)
  │            ├──N:N──► TeamMember (via PortalTeamAssignment)
  │            └──1:N──► EventEngagement (portal)
  │                         │   bridges portal ↔ event; every event gets one (is_active=True for at
  │                         │   most one per portal — see "Since the last update" above)
  │                         ├──1:1──► Event (events)                 ◄─ same Event as below
  │                         ├──1:N──► Meeting (meetings)
  │                         │            ├──1:N──► MeetingPrepItem
  │                         │            │            └──1:N──► PrepItemField
  │                         │            │                         ├──1:1──► PrepItemResponse
  │                         │            │                         └──1:N──► PrepItemFileUpload
  │                         │            └──1:1──► MeetingNotes
  │                         ├──1:N──► Conversation (conversations)
  │                         │            (ConversationReply — planned, not implemented)
  │                         ├──1:N──► Reminder (reminders)
  │                         ├──1:N──► Document (documents — generic upload registry, GenericFK to source;
  │                         │            object_id is a CharField, supports int- or UUID-PK sources)
  │                         ├──1:N──► Notification (notifications)
  │                         ├──1:N──► ClientDocument (document_hub)
  │                         ├──1:1──► PaymentSchedule (document_hub)
  │                         │            └──1:N──► PaymentMilestone
  │                         ├──1:N──► Invoice (document_hub)
  │                         └──1:N──► Receipt (document_hub)
  │
  └──1:N──► Event (events)                              [User is `celebrant`]
                 ├──1:N──► EventDay
                 ├──1:1──► EventEngagement (portal)         [same object as above]
                 ├──1:1──► EventBudget (budgets)
                 │            ├──1:N──► BudgetCategory
                 │            └──1:N──► BudgetPayment
                 └──1:N──► EventContact (contacts)          [also FK'd to EventDay]

InquiryForm (inquiries) — standalone lead-capture form, no FK to User or Event

```
