# Tenant Admin Page Specification

## Objective

Implement a polished **Tenant Admin landing page** that serves as the central entry point for tenant administrators to manage tenant-level configuration, governance, security, and reusable assets.

This page is a **hub page**, not a full settings form. It should be elegant, intuitive, highly scannable, and optimized for fast navigation into dedicated sub-pages.

---

## Primary Route

`/tenants/:tenantId/admin`

---

## Design Intent

The page should feel like a modern SaaS control plane:

- clean
- minimal
- structured
- professional
- easy to scan in under 10 seconds
- optimized for admin workflows

Do **not** place large forms, dense data tables, or complex charts directly on this page.

---

## Core UX Principles

1. This page is the **tenant admin entry point**.
2. Every major tenant admin capability should be reachable in **one click**.
3. Most common actions should appear **above the fold**.
4. Security and governance should be treated as first-class features.
5. The page must work well for both:
   - a brand new tenant with no data
   - a mature tenant with many configurations
6. The layout should favor **summary + navigation**, not inline editing.

---

## Page Layout

### Overall Structure

Use a responsive page layout with:

- **Header area**
- **Summary stats strip**
- **Admin search/filter bar**
- **Main grouped card layout**
- **Right sidebar on desktop**

### Responsive Layout Rules

#### Desktop
Use a **12-column layout**:
- main content area: **8 columns**
- right sidebar: **4 columns**

#### Tablet
- stack content vertically
- move sidebar content below main content

#### Mobile
- single-column layout
- stack header actions
- all cards full width

---

## Header Area

### Render the following

#### Breadcrumb
`Tenants / {Tenant Name} / Admin`

#### Title
`Tenant Admin`

#### Subtitle
`Manage tenant-level configuration, governance, security, and reusable assets.`

#### Top-right Actions
1. **Primary button:** `Create Orchestration`
2. **Secondary button:** `Manage Access`

### Header Action Routes

- `Create Orchestration` → `/tenants/:tenantId/orchestrations/new`
- `Manage Access` → `/tenants/:tenantId/admin/access`

---

## Summary Stats Strip

Render a horizontal strip of compact clickable summary cards directly below the header.

### Summary Cards

1. **Orchestrations**
   - label: `Orchestrations`
   - value: orchestration count
   - route: `/tenants/:tenantId/orchestrations`

2. **Prompt Library**
   - label: `Prompt Library`
   - value: prompt count
   - route: `/tenants/:tenantId/admin/prompts`

3. **Form Schemas**
   - label: `Form Schemas`
   - value: form schema count
   - route: `/tenants/:tenantId/admin/forms`

4. **Output Schemas**
   - label: `Structured Output Schemas`
   - value: output schema count
   - route: `/tenants/:tenantId/admin/output-schemas`

5. **Auth Profiles**
   - label: `Authentication Profiles`
   - value: auth profile count
   - route: `/tenants/:tenantId/admin/auth-profiles`

6. **Policies**
   - label: `Policies`
   - value: policy count
   - route: `/tenants/:tenantId/admin/policies`

### Summary Strip Behavior

- each stat card is fully clickable
- cards should support loading skeletons
- cards should support zero values
- cards should have subtle hover state and keyboard focus state

---

## Search / Quick Find

Place a single search field below the summary strip.

### Search Configuration

- label: `Search tenant admin features`
- placeholder: `Search pages, configs, prompts, schemas, or policies`

### Behavior

- filters visible admin feature cards by title and description
- should be client-side for now
- no backend search required for initial version

---

## Main Content Sections

Render the main content as grouped feature-card sections.

### Section Order

1. **Build & Reuse**
2. **Governance & Security**
3. **Tenant Setup**

Each section should have:
- section heading
- optional short intro text
- responsive grid of feature cards

Each feature card must include:
- icon
- title
- short description
- optional metadata line
- primary action button
- optional secondary text link
- full-card click behavior

Cards should be uniform height.

---

# Section 1: Build & Reuse

## 1. Orchestrations

**Title:** `Orchestrations`

**Description:**  
Create and manage tenant orchestration workflows and reusable builder assets.

**Primary Action:**
- `View Orchestrations` → `/tenants/:tenantId/orchestrations`

**Secondary Action:**
- `Create New` → `/tenants/:tenantId/orchestrations/new`

---

## 2. Prompt Library

**Title:** `Prompt Library`

**Description:**  
Manage reusable prompts, system instructions, and prompt templates.

**Primary Action:**
- `Open Prompt Library` → `/tenants/:tenantId/admin/prompts`

**Secondary Action:**
- `Create Prompt` → `/tenants/:tenantId/admin/prompts/new`

---

## 3. Form Schemas

**Title:** `Form Schemas`

**Description:**  
Define reusable input form schemas for orchestration steps and user interactions.

**Primary Action:**
- `View Form Schemas` → `/tenants/:tenantId/admin/forms`

**Secondary Action:**
- `Create Schema` → `/tenants/:tenantId/admin/forms/new`

---

## 4. Structured Output Schemas

**Title:** `Structured Output Schemas`

**Description:**  
Manage reusable typed output schemas for LLM responses and workflow contracts.

**Primary Action:**
- `View Output Schemas` → `/tenants/:tenantId/admin/output-schemas`

**Secondary Action:**
- `Create Schema` → `/tenants/:tenantId/admin/output-schemas/new`

---

# Section 2: Governance & Security

## 5. Policies

**Title:** `Policies`

**Description:**  
Configure tenant-wide policies such as error handling, retries, retention, and execution controls.

**Primary Action:**
- `Manage Policies` → `/tenants/:tenantId/admin/policies`

**Secondary Action:**
- `Edit Defaults` → `/tenants/:tenantId/admin/policies/defaults`

---

## 6. Authentication Profiles

**Title:** `Authentication Profiles`

**Description:**  
Manage secret-backed connection profiles and credential references for integrations and tools.

**Primary Action:**
- `View Profiles` → `/tenants/:tenantId/admin/auth-profiles`

**Secondary Action:**
- `Create Profile` → `/tenants/:tenantId/admin/auth-profiles/new`

**Important:**
- do not expose raw secret values on this page
- show only profile metadata / status / usage summary

---

## 7. Users & Access

**Title:** `Users & Access`

**Description:**  
Manage tenant admins, roles, permissions, and access controls.

**Primary Action:**
- `Manage Access` → `/tenants/:tenantId/admin/access`

**Secondary Action:**
- `Invite User` → `/tenants/:tenantId/admin/access/invite`

---

## 8. Audit Activity

**Title:** `Audit Activity`

**Description:**  
Review administrative changes, configuration updates, and publishing history.

**Primary Action:**
- `View Audit Log` → `/tenants/:tenantId/admin/audit`

**Secondary Action:**
- `Recent Changes` → `/tenants/:tenantId/admin/audit?filter=recent`

---

# Section 3: Tenant Setup

## 9. Tenant Config

**Title:** `Tenant Config`

**Description:**  
Manage global tenant settings, defaults, feature flags, and shared configuration values.

**Primary Action:**
- `Open Tenant Config` → `/tenants/:tenantId/admin/config`

**Secondary Action:**
- `Edit Global Variables` → `/tenants/:tenantId/admin/config/variables`

---

## 10. Environments

**Title:** `Environments`

**Description:**  
Manage tenant environments such as dev, test, and prod, including environment-specific overrides.

**Primary Action:**
- `Manage Environments` → `/tenants/:tenantId/admin/environments`

**Secondary Action:**
- `View Overrides` → `/tenants/:tenantId/admin/environments/overrides`

---

## 11. Integrations

**Title:** `Integrations`

**Description:**  
Configure approved tools, MCP servers, external APIs, and shared integration bindings.

**Primary Action:**
- `View Integrations` → `/tenants/:tenantId/admin/integrations`

**Secondary Action:**
- `Add Integration` → `/tenants/:tenantId/admin/integrations/new`

---

## Right Sidebar

Show a right sidebar on desktop only. On tablet/mobile, move this content below the main content sections.

### Sidebar Card 1: Recent Activity

Display the 5 most recent tenant admin actions.

Example items:
- prompt updated
- policy changed
- authentication profile created
- schema published
- orchestration created

**Footer link:**
- `View Full Audit Log` → `/tenants/:tenantId/admin/audit`

---

### Sidebar Card 2: Quick Actions

Include compact buttons/links for:

- `Create Orchestration` → `/tenants/:tenantId/orchestrations/new`
- `Create Prompt` → `/tenants/:tenantId/admin/prompts/new`
- `Create Form Schema` → `/tenants/:tenantId/admin/forms/new`
- `Create Output Schema` → `/tenants/:tenantId/admin/output-schemas/new`
- `Create Auth Profile` → `/tenants/:tenantId/admin/auth-profiles/new`

---

## Recommended Route Map

The page should link to the following child pages:

- `/tenants/:tenantId/orchestrations`
- `/tenants/:tenantId/orchestrations/new`
- `/tenants/:tenantId/admin/prompts`
- `/tenants/:tenantId/admin/prompts/new`
- `/tenants/:tenantId/admin/forms`
- `/tenants/:tenantId/admin/forms/new`
- `/tenants/:tenantId/admin/output-schemas`
- `/tenants/:tenantId/admin/output-schemas/new`
- `/tenants/:tenantId/admin/policies`
- `/tenants/:tenantId/admin/policies/defaults`
- `/tenants/:tenantId/admin/auth-profiles`
- `/tenants/:tenantId/admin/auth-profiles/new`
- `/tenants/:tenantId/admin/access`
- `/tenants/:tenantId/admin/access/invite`
- `/tenants/:tenantId/admin/audit`
- `/tenants/:tenantId/admin/config`
- `/tenants/:tenantId/admin/config/variables`
- `/tenants/:tenantId/admin/environments`
- `/tenants/:tenantId/admin/environments/overrides`
- `/tenants/:tenantId/admin/integrations`
- `/tenants/:tenantId/admin/integrations/new`

---

## Visual Design Requirements

Use a refined admin-console visual style.

### Styling Guidance

- soft background surfaces
- subtle borders
- restrained shadows
- generous spacing
- rounded cards
- clean typography hierarchy
- consistent iconography
- low-noise color usage
- polished but not flashy

### Avoid

- dense tables on the landing page
- large charts
- too many colors
- overly verbose card descriptions
- inline editing panels
- modal-heavy interactions

---

## Interaction Requirements

### Feature Cards

Each feature card must:
- be fully clickable
- include a visible CTA button
- include hover and focus states
- support disabled and loading states

### Permissions

If the current user lacks access to a section:
- render the card in a disabled state
- keep the card visible
- show helper text such as: `You do not have access to this section`
- do not remove the card entirely

### Empty States

If no entities exist yet, still render the card.

Examples:
- `No prompts created yet`
- `No form schemas created yet`
- `No authentication profiles configured yet`

Keep the create CTA visible even when empty.

---

## Suggested React Component Breakdown

Implement using clean reusable components.

Recommended components:

- `TenantAdminPage`
- `TenantAdminHeader`
- `TenantSummaryStats`
- `TenantSummaryStatCard`
- `TenantAdminSearchBar`
- `TenantAdminSection`
- `TenantAdminFeatureCard`
- `TenantAdminSidebar`
- `RecentActivityCard`
- `QuickActionsCard`

### Suggested Supporting Types

- `TenantAdminSummary`
- `TenantAdminFeature`
- `TenantAdminSectionModel`
- `RecentActivityItem`

---

## Suggested Data Shape

Use a simple view-model shape for the page.

```ts
export interface TenantAdminSummary {
  orchestrations: number;
  prompts: number;
  formSchemas: number;
  outputSchemas: number;
  authProfiles: number;
  policies: number;
}

export interface TenantAdminFeature {
  id: string;
  title: string;
  description: string;
  route: string;
  secondaryRoute?: string;
  primaryActionLabel: string;
  secondaryActionLabel?: string;
  icon?: React.ReactNode;
  metadata?: string;
  disabled?: boolean;
  emptyText?: string;
}

export interface RecentActivityItem {
  id: string;
  text: string;
  timestamp: string;
  route?: string;
}
```

---

## Accessibility Requirements

- use semantic headings in correct order
- all clickable elements must be keyboard accessible
- visible focus states are required
- buttons and links must have clear accessible labels
- maintain strong color contrast
- do not rely on color alone to convey meaning

---

## Implementation Guardrails

- use reusable React components
- avoid large monolithic page files
- avoid duplicated card markup
- keep route definitions centralized
- keep mock data separate from presentational components
- prefer composable layout and card primitives
- keep SCSS/CSS modular and clean
- do not hardcode styles inline unless required by the design system

---

## Acceptance Criteria

1. The page renders at `/tenants/:tenantId/admin`.
2. The page shows breadcrumb, title, subtitle, and top actions.
3. The page displays clickable summary stat cards.
4. The page includes a search field that filters visible admin cards.
5. The page displays the three grouped sections:
   - Build & Reuse
   - Governance & Security
   - Tenant Setup
6. Each feature card contains:
   - title
   - description
   - primary CTA
   - optional secondary action
   - route behavior
7. The page includes a desktop sidebar with:
   - Recent Activity
   - Quick Actions
8. The layout is fully responsive across desktop, tablet, and mobile.
9. Empty and permission-restricted states are supported.
10. The page feels elegant, minimal, intuitive, and admin-focused.

---

## Copilot Build Prompt

Create a React page for route `/tenants/:tenantId/admin` called `TenantAdminPage`.

Build it as a polished SaaS admin landing page for tenant administrators.

Requirements:
- responsive layout with header, summary stats, search, grouped feature-card sections, and right sidebar
- use clean reusable components
- cards must be fully clickable and keyboard accessible
- include route-based buttons/links for each admin area
- use elegant restrained styling with subtle borders, spacing, and rounded cards
- do not put full forms or dense tables on this page
- optimize for clarity, scanability, and intuitive navigation
- structure the code so future pages can reuse the same card and section components

