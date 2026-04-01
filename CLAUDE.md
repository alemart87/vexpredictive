# Vex People Predictive - Design System & Project Guide

## Project Overview
Vex People Predictive es una plataforma empresarial multi-tenant de entrenamientos, evaluacion predictiva del talento y ayuda en linea. Full-stack app con Flask + PostgreSQL desplegado en Render.com via Docker. Desarrollado por VEX I+D.

## Tech Stack
- **Backend:** Flask + SQLAlchemy + Flask-Login + Gunicorn
- **Database:** PostgreSQL (Render)
- **Frontend:** Jinja2 templates + Vanilla JS
- **CMS Editor:** Quill.js
- **Charts:** Chart.js
- **Deployment:** Docker on Render.com

## Color Palette (CSS Variables - Voicenter Brand)

```css
:root {
    --rojo-vex: #E6332A;        /* Primary: CTA buttons, headers, accents (Pantone 485C) */
    --naranja-vex: #F39200;     /* Gradients, secondary warm (Pantone 144C) */
    --purpura-vex: #662483;     /* Headings, accents (Pantone 526C) */
    --cyan-vex: #00B2BF;        /* Nav, buttons, footer (Pantone 7466C) */
    --rojo-hover: #C42A23;      /* Red hover state */
    --cyan-oscuro: #009BA8;     /* Cyan hover state */
    --gris-claro: #f2f2f2;      /* Light backgrounds, alternating rows */
    --gris-fondo: #f4f4f4;      /* Card backgrounds */
    --gris-page: #f9f9f9;       /* Page background */
    --gris-texto: #333333;      /* Body text */
    --gris-meta: #888888;       /* Metadata, secondary text */
    --negro: #000000;
    --blanco: #ffffff;
    --sombra-sm: 0 2px 5px rgba(0,0,0,0.1);
    --sombra-md: 0 4px 8px rgba(0,0,0,0.2);
    --sombra-lg: 0 8px 16px rgba(0,0,0,0.2);
    --sombra-roja: 0 8px 20px rgba(230, 51, 42, 0.4);
}
```

## Typography
- **Primary:** `'Arial', sans-serif`
- **Line height:** 1.6
- **Body text:** 16px, color var(--gris-texto)

## Multi-Tenant Architecture (Operativas)
Each Operativa is a tenant with its own:
- Users (Coordinador, Supervisor, Operador)
- Content and Categories
- Training Scenarios
- Custom branding (logo, colors)

## Authentication Roles (Hierarchy)
1. **SuperAdmin:** Configured via .env only. Full platform access. Manages Operativas.
2. **Coordinador:** Created by SuperAdmin within an Operativa. Manages users, content, training, VEX within their Operativa. Can customize Operativa branding.
3. **Supervisor:** Created by Coordinador. Can view content, do trainings, request document reviews.
4. **Operador:** Created by Coordinador. Can view content and do trainings.

No self-registration. All users created by admins.

## Component Patterns

### Header
```css
background: linear-gradient(135deg, var(--rojo-vex), var(--naranja-vex));
color: var(--blanco);
```

### Navigation
```css
background-color: var(--cyan-vex);
/* Links: white, hover -> background var(--rojo-vex) */
```

### Cards
```css
background: var(--blanco);
border: 1px solid var(--rojo-vex);
border-radius: 10px;
```

### Buttons
```css
.btn-primary: background var(--rojo-vex);
.btn-secondary: background var(--cyan-vex);
```

## Responsive Breakpoints (Mobile-First)
- **Base (mobile):** < 768px -- 1 column, hamburger menu
- **Tablet:** 768px -- 2 columns
- **Desktop:** 1024px -- 3-4 columns, full nav

## Naming Conventions
- Database: snake_case (page_views, click_events, operativas)
- Python: snake_case (get_user, create_content)
- URLs: kebab-case (/admin/contents, /api/track/pageview)
- Templates: snake_case (content_edit.html)
- CSS classes: kebab-case (.card-container, .search-bar)

## Important Rules
- All UI text in Spanish
- Copyright: "VEX I+D"
- Always use CSS variables for colors, never hardcode hex values in new code
- Images stored in static/imagenes/
- All pages must include tracking.js for analytics
- Mobile-first: design for small screens, enhance for larger
- Operativas can override colors via context processor (op_primary, op_secondary)
- Content, Categories, and Scenarios are scoped to Operativas via operativa_id
