# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Vue 3, TypeScript, Vite, Vue Router, lucide-vue-next, native fetch, Docker and Nginx, with the existing FastAPI backend as the source of truth.

## Users

- Customers submit support tickets, read sent replies, cancel open tickets, and rate closed tickets.
- Agents process tickets, use AI analysis and reply suggestions, review replies, send approved replies, and search the knowledge base.
- Admins have agent capabilities plus knowledge-base management.

## Product Purpose

AI Support Workbench is an internal customer-support workbench for handling tickets through a controlled lifecycle: intake, analysis, human review, approved response, closure, and evaluation. Success means support staff can scan and act quickly while every consequential action remains explicit and auditable.

## Positioning

The product combines a ticket state machine, asynchronous AI assistance, retrieval-backed knowledge search, and mandatory human review in one operational workflow. AI can assist with analysis and drafting but cannot bypass human approval or backend authorization.

## Operating Context

This is a high-frequency internal operations tool used on desktop screens and mobile browsers. Users work from tables, filters, ticket detail panels, status indicators, review controls, asynchronous task states, and paginated knowledge documents. The backend API is the source of truth for permissions, ticket state, and data visibility.

## Capabilities and Constraints

- Authentication uses POST /auth/login and GET /auth/me with a browser-only access token stored in memory or sessionStorage.
- Customers see only their own tickets and sent replies. Agents and admins see staff ticket data.
- Ticket status changes must use the transition API. The frontend must not replace backend RBAC.
- AI analysis and reply suggestions are asynchronous and must be polled every three seconds while pending or processing, then stopped on unmount.
- AI suggestions are labeled and can only be sent after human review.
- Knowledge uploads are restricted to sanitized TXT/PDF files; real private documents and secrets must never be committed.
- API errors must surface safe messages for 400, 401, 403, 404, 409, 422, network failure, loading, empty, and success states.
- Destructive actions require a second confirmation.

## Brand Commitments

The product name is AI Support Workbench. The interface should feel like a focused internal operations console, not a marketing landing page.

## Evidence on Hand

The existing FastAPI routes, Pydantic schemas, service layer, tests, and docs in this repository are the only product evidence. No frontend visual system or production support data exists yet; UI demonstrations must use clearly synthetic local display states or live API responses, never fabricated customer records presented as real data.

## Product Principles

- State and permissions must be visible before action.
- Human review remains the gate for AI-assisted replies.
- Dense information is useful when grouping and hierarchy stay clear.
- Every loading, empty, error, success, forbidden, and not-found state teaches the next action.
- Backend contracts decide access and business state; frontend controls only presentation and flow.

## Accessibility & Inclusion

Interactive controls need keyboard-visible focus, text plus icon or color for status, resilient long-text layout, readable contrast, and mobile layouts that do not require horizontal page scrolling. Icon-only controls require accessible names or tooltips.
