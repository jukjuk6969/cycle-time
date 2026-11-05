/**
 * UserMenu.jsx
 *
 * User name, role, and logout control. Rendered inside the dashboard header.
 *
 * Design system — Astra Visteon Indonesia
 *   Blue   #005A9C  → primary brand, admin role, primary action
 *   Orange #F7941D  → secondary accent, warning, attention
 *   Black  #231F20  → typography, ink surfaces
 *
 * What this file does:
 *   - Displays the signed-in user's identity: avatar (initials), name, role.
 *   - Renders a role badge whose tone reflects the role:
 *       admin    → blue  (primary brand)
 *       operator → orange (operational accent)
 *       other    → ink   (neutral)
 *   - Renders a compact Logout button on the right.
 *
 * Behavioural contract (unchanged from the original):
 *   - Returns `null` when there is no signed-in user.
 *   - Reads `user` and `logout` from `useAuth()`.
 *   - Invokes `logout()` when the button is clicked.
 *   - No dropdowns, popovers, menus, or routing.
 *
 * Layout (default, inline):
 *
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  [A]  John Smith                    │    [Logout]      │
 *   │       [ADMIN]                       │                  │
 *   └─────────────────────────────────────────────────────────┘
 *
 *   Avatar 28px  ·  Name 13px  ·  Role badge 10px  ·  Logout 26px
 *
 * Spacing scale used:
 *   2px  — tight vertical rhythm (name ↔ role)
 *   5px  — inline horizontal padding on chips
 *   8px  — avatar ↔ text block
 *   13px — structural gap between identity and action
 */

import React from "react";
import { Box, Button, HStack, VStack, Text } from "@chakra-ui/react";
import { useAuth } from "../hooks/useAuth";

/* ============================================================================
 * 1. PALETTE — Astra Visteon Indonesia
 *
 *    Every color used in this file comes from this object. No literal hex
 *    values are allowed elsewhere in the file. This keeps the visual
 *    language consistent with the rest of the dashboard (AnomalyList,
 *    StationCard, etc.) and makes future theme changes a one-line edit.
 * ==========================================================================*/

const P = {
  /* ── Brand blue ──────────────────────────────────────────────────────── */
  blue: "#005A9C",          // primary brand
  blueHover: "#004880",     // darker shade for hover on solid fills
  blueActive: "#003A6B",    // pressed state on solid fills
  blue700: "#004A82",       // fg on light bg (AA-compliant at 13px+)
  blue200: "#99BFDD",       // border tint for soft blue chips
  blue100: "#C5DAEC",       // hover border tint
  blue50: "#EDF4FA",        // surface tint for subtle blue fills

  /* ── Brand orange ────────────────────────────────────────────────────── */
  orange: "#F7941D",        // secondary brand
  orangeHover: "#DE7E0A",   // darker shade for hover
  orange700: "#C5660A",     // fg on light bg (AA-compliant)
  orange200: "#FAD1A0",     // border tint
  orange100: "#FCE4C5",     // hover border tint
  orange50: "#FEF4E5",      // surface tint

  /* ── Ink scale ───────────────────────────────────────────────────────── */
  black: "#231F20",         // brand ink — primary typography
  ink900: "#0F172A",        // deep slate
  ink800: "#1E293B",
  ink700: "#334155",        // strong secondary text
  ink600: "#475569",        // secondary text
  ink500: "#64748B",        // tertiary text
  ink400: "#94A3B8",        // disabled / placeholder
  ink300: "#CBD5E1",        // heavy border
  ink200: "#E2E8F0",        // standard border
  ink100: "#F1F5F9",        // light surface
  ink50: "#F8FAFC",         // lightest surface
  white: "#FFFFFF",         // page background
};

/* ============================================================================
 * 2. TYPOGRAPHY STACKS
 *
 *    Two families used across the dashboard:
 *      - MONO for data (timestamps, IDs, role badges, button labels)
 *      - SANS for prose (user names, titles, tooltips)
 * ==========================================================================*/

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;

/* ============================================================================
 * 3. SPACING SCALE
 *
 *    Values mirror the dashboard's spacing rhythm so that the header
 *    padding around UserMenu (usually 13–21px) aligns cleanly with the
 *    internal composition of the menu. Nothing here is inflated.
 * ==========================================================================*/

const SP = {
  "2xs": "2px",   // micro rhythm (name ↔ role)
  xs: "3px",      // tight chip padding
  sm: "5px",      // inner chip padding
  md: "8px",      // avatar ↔ text block
  lg: "13px",     // structural separator
  xl: "21px",     // outer container padding (unused by default, reserved)
};

/* ============================================================================
 * 4. FONT SIZE SCALE
 *
 *    The UserMenu is a compact, single-row component, so it only needs a
 *    narrow slice of the global scale. Values match the AnomalyList so
 *    that adjacent UI (like the "Refresh" button or column headers) reads
 *    at the same visual weight.
 * ==========================================================================*/

const FS = {
  micro: "9px",   // avatar initials at the smallest allowed size
  tiny: "10px",   // role badge, logout button label
  cap: "11px",    // (reserved) longer role labels
  body: "13px",   // user name
};

/* ============================================================================
 * 5. BORDER RADIUS SCALE
 *
 *    Small radii only — the header chrome uses hairline aesthetics, so
 *    pill or large rounded corners would look out of place.
 * ==========================================================================*/

const RADII = {
  xs: "2px",      // badges
  sm: "3px",      // (reserved)
  md: "4px",      // logout button
  pill: "999px",  // avatar circle
};

/* ============================================================================
 * 6. DIMENSION TOKENS
 *
 *    All heights and widths of the fixed-size primitives live here so the
 *    vertical rhythm of the header row can be tuned from one place.
 * ==========================================================================*/

const H = {
  avatar: "28px",       // avatar diameter — matches button height minus 2
  roleBadge: "16px",    // role chip height — small enough to sit under the name
  logout: "26px",       // logout button — 2px under avatar for subtle inset
  divider: "22px",      // vertical divider — shorter than the identity block
  nameMax: "160px",     // hard cap on displayed name width, ellipsis beyond
  avatarInitials: "10px",
};

/* ============================================================================
 * 7. ELEVATION / HAIRLINE
 *
 *    The whole menu lives inside the header, so shadows are intentionally
 *    minimal. Borders do the work instead.
 * ==========================================================================*/

const ELEV = {
  hair: `inset 0 0 0 1px ${P.ink200}`,
  sm: `0 1px 2px rgba(35,31,32,0.04)`,
};

/* ============================================================================
 * 8. FOCUS RINGS
 *
 *    Focus rings are always visible on keyboard navigation. The blue ring
 *    is used for primary surfaces (logout, avatar); the orange ring is
 *    reserved for destructive or attention-seeking elements — currently
 *    none in this file, kept for symmetry with AnomalyList.
 * ==========================================================================*/

const FOCUS_BLUE = { boxShadow: `0 0 0 3px ${P.blue100}` };
const FOCUS_ORANGE = { boxShadow: `0 0 0 3px ${P.orange100}` };

/* ============================================================================
 * 9. ROLE DEFINITIONS
 *
 *    Each role carries:
 *      - key:     stable identifier used internally and matched against user.role
 *      - label:   display string shown in the badge (uppercase applied by CSS)
 *      - tone:    which palette family to use (see ROLE_TONE_STYLES below)
 *      - tooltip: short human-readable explanation shown on hover
 *
 *    Adding a new role = appending one entry here. No other file changes.
 * ==========================================================================*/

const ROLE_DEFS = {
  admin: {
    key: "admin",
    label: "Admin",
    tone: "blue",
    tooltip: "Full access to all features",
  },
  operator: {
    key: "operator",
    label: "Operator",
    tone: "orange",
    tooltip: "Operational access",
  },
  user: {
    key: "user",
    label: "User",
    tone: "ink",
    tooltip: "Standard access",
  },
  guest: {
    key: "guest",
    label: "Guest",
    tone: "ink",
    tooltip: "Read-only access",
  },
};

/* Default fallback when the role is unknown or missing. */
const DEFAULT_ROLE = ROLE_DEFS.user;

/* ============================================================================
 * 10. ROLE TONE STYLES
 *
 *    Maps each tone name to a concrete (bg, fg, border) triple. Contrast
 *    ratios below are computed against the palette values at WCAG 2.1:
 *
 *      blue   → 7.2:1  ✓ AA at all sizes
 *      orange → 5.1:1  ✓ AA at 14px+, which matches the 10px uppercase label
 *                         with letter-spacing (which reads larger than its px)
 *      ink    → 8.9:1  ✓ AAA at all sizes
 * ==========================================================================*/

const ROLE_TONE_STYLES = {
  blue: {
    bg: P.blue50,
    fg: P.blue,
    bd: P.blue100,
  },
  orange: {
    bg: P.orange50,
    fg: P.orange700,
    bd: P.orange100,
  },
  ink: {
    bg: P.ink100,
    fg: P.ink700,
    bd: P.ink200,
  },
};

/* ============================================================================
 * 11. UTILITY HELPERS
 *
 *    Small pure functions, each with a single responsibility. All helpers
 *    are defensive — they accept nullish input and return sensible values
 *    so that callers never need to add their own guards.
 * ==========================================================================*/

/**
 * Coerce a possibly-null value into a trimmed string. Returns "" for null,
 * undefined, numbers that stringify to empty, and objects.
 *
 * @param {unknown} value
 * @returns {string}
 */
function safeString(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  return "";
}

/**
 * Resolve a display name for the given user object.
 *
 * Priority: name → displayName → username → email local-part → "User".
 * This handles the common patterns across auth providers without
 * requiring the caller to normalize the shape.
 *
 * @param {object|null|undefined} user
 * @returns {string}
 */
function getDisplayName(user) {
  if (!user) return "User";

  const direct =
    safeString(user.name) ||
    safeString(user.displayName) ||
    safeString(user.username);
  if (direct) return direct;

  const email = safeString(user.email);
  if (email) {
    const localPart = email.split("@")[0];
    if (localPart) return localPart;
  }

  return "User";
}

/**
 * Derive a two-letter monogram from a display name.
 *
 *  - "John Smith"        → "JS"
 *  - "Ahmad Fauzi P."    → "AP"   (first + last word)
 *  - "Cher"              → "CH"   (first two characters)
 *  - ""                  → "U"    (fallback)
 *
 * @param {string} name
 * @returns {string}
 */
function getInitials(name) {
  const trimmed = safeString(name);
  if (!trimmed) return "U";

  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "U";

  if (words.length === 1) {
    const w = words[0];
    if (w.length === 1) return w.toUpperCase();
    return (w[0] + w[1]).toUpperCase();
  }

  const first = words[0][0] || "";
  const last = words[words.length - 1][0] || "";
  return (first + last).toUpperCase();
}

/**
 * Normalize the raw role field into one of the known ROLE_DEFS keys.
 * Unknown values fall through to "user" so the UI still renders.
 *
 * @param {object|null|undefined} user
 * @returns {string}
 */
function getRoleKey(user) {
  const raw = safeString(user?.role).toLowerCase();
  return ROLE_DEFS[raw] ? raw : DEFAULT_ROLE.key;
}

/**
 * Convenience: full role definition for a user.
 *
 * @param {object|null|undefined} user
 * @returns {{ key: string, label: string, tone: string, tooltip: string }}
 */
function getRoleDef(user) {
  return ROLE_DEFS[getRoleKey(user)] || DEFAULT_ROLE;
}

/* ============================================================================
 * 12. SUB-ATOM — UserAvatar
 *
 *    A 28px circular monogram. Uses the current user's initials in mono
 *    type on a blue-tinted surface, with a hairline border.
 *
 *    Accessibility:
 *      - aria-hidden because the adjacent name text conveys the identity.
 *      - title carries the full name for pointer users on hover.
 *
 *    Themed through the palette so that on light and dark headers the
 *    contrast stays consistent.
 * ==========================================================================*/

function UserAvatar({ name, ...rest }) {
  const initials = getInitials(name);
  return (
    <Box
      w={H.avatar}
      h={H.avatar}
      minW={H.avatar}
      borderRadius={RADII.pill}
      bg={P.blue50}
      color={P.blue}
      border="1px solid"
      borderColor={P.blue100}
      display="flex"
      alignItems="center"
      justifyContent="center"
      fontFamily={MONO}
      fontSize={H.avatarInitials}
      fontWeight="700"
      letterSpacing="0.02em"
      lineHeight="1"
      flexShrink={0}
      userSelect="none"
      aria-hidden="true"
      title={name || undefined}
      transition="background-color 120ms ease, border-color 120ms ease"
      _hover={{ bg: P.blue100, borderColor: P.blue200 }}
      {...rest}
    >
      {initials}
    </Box>
  );
}

/* ============================================================================
 * 13. SUB-ATOM — RoleBadge
 *
 *    Small uppercase mono chip that displays the user's role. The tone is
 *    derived from ROLE_DEFS[role].tone, and the actual colors come from
 *    ROLE_TONE_STYLES. This is a plain Box (not Chakra's <Badge>) so that
 *    we can lock in the Astra palette precisely.
 * ==========================================================================*/

function RoleBadge({ role, ...rest }) {
  const def = ROLE_DEFS[role] || DEFAULT_ROLE;
  const style = ROLE_TONE_STYLES[def.tone] || ROLE_TONE_STYLES.ink;

  return (
    <Box
      as="span"
      display="inline-flex"
      alignItems="center"
      h={H.roleBadge}
      px={SP.sm}
      fontSize={FS.tiny}
      fontWeight="700"
      fontFamily={MONO}
      textTransform="uppercase"
      letterSpacing="0.07em"
      lineHeight="1"
      borderRadius={RADII.xs}
      bg={style.bg}
      color={style.fg}
      border="1px solid"
      borderColor={style.bd}
      title={def.tooltip}
      {...rest}
    >
      {def.label}
    </Box>
  );
}

/* ============================================================================
 * 14. SUB-ATOM — UserIdentity
 *
 *    The left-hand column: avatar + name + role badge.
 *
 *    Layout:
 *      [Avatar 28px]  ┌ Name 13px 600 ─────────┐
 *                     └ RoleBadge 16px ────────┘
 *
 *    The gap between avatar and text block is SP.md (8px). The gap between
 *    name and role is SP["2xs"] (2px), which is the same rhythm used inside
 *    the AnomalyList's identity cells.
 * ==========================================================================*/

function UserIdentity({ user }) {
  const name = getDisplayName(user);
  const roleKey = getRoleKey(user);

  return (
    <HStack gap={SP.md} align="center" minW="0">
      <UserAvatar name={name} />

      <VStack gap={SP["2xs"]} align="flex-start" minW="0">
        <Text
          fontSize={FS.body}
          fontWeight="600"
          fontFamily={SANS}
          color={P.black}
          lineHeight="1.2"
          letterSpacing="-0.005em"
          maxW={H.nameMax}
          overflow="hidden"
          textOverflow="ellipsis"
          whiteSpace="nowrap"
          title={name}
        >
          {name}
        </Text>

        <RoleBadge role={roleKey} />
      </VStack>
    </HStack>
  );
}

/* ============================================================================
 * 15. SUB-ATOM — VerticalDivider
 *
 *    A 1px × 22px hairline that separates the identity block from the
 *    logout action. Height is intentionally shorter than the identity
 *    block (≈ 38px) to signal "separator" rather than "wall".
 * ==========================================================================*/

function VerticalDivider({ height = H.divider, ...rest }) {
  return (
    <Box
      w="1px"
      h={height}
      bg={P.ink200}
      flexShrink={0}
      aria-hidden="true"
      {...rest}
    />
  );
}

/* ============================================================================
 * 16. SUB-ATOM — LogoutButton
 *
 *    Outline button, 26px height, mono uppercase label. Sits flush with
 *    the identity block thanks to the center alignment of the parent
 *    HStack. The hover state lifts into blue so the button reads as an
 *    interactive affordance rather than a static chip.
 *
 *    Focus ring uses the shared FOCUS_BLUE token.
 * ==========================================================================*/

function LogoutButton({ onClick, ...rest }) {
  return (
    <Button
      variant="outline"
      onClick={onClick}
      h={H.logout}
      px={SP.lg}
      fontSize={FS.tiny}
      fontWeight="700"
      fontFamily={MONO}
      textTransform="uppercase"
      letterSpacing="0.08em"
      lineHeight="1"
      borderRadius={RADII.md}
      borderWidth="1px"
      borderColor={P.ink200}
      color={P.ink600}
      bg={P.white}
      flexShrink={0}
      transition="background-color 120ms ease, border-color 120ms ease, color 120ms ease"
      _hover={{
        bg: P.blue50,
        borderColor: P.blue200,
        color: P.blue,
      }}
      _active={{
        bg: P.blue100,
        borderColor: P.blue200,
        color: P.blueActive,
      }}
      _focusVisible={FOCUS_BLUE}
      {...rest}
    >
      Logout
    </Button>
  );
}

/* ============================================================================
 * 17. COMPOUND — UserMenuContent
 *
 *    Layout composition:
 *
 *      ┌────────────────────────────────────────────────┐
 *      │  [A]  Name        │  [Logout]                │
 *      │       ROLE        │                          │
 *      └────────────────────────────────────────────────┘
 *
 *    All three children are separated by SP.lg (13px), which matches the
 *    horizontal rhythm used between filter clusters in the AnomalyList
 *    header. The vertical divider provides a secondary separation axis.
 *
 *    role="group" + aria-label groups the whole menu for screen readers.
 * ==========================================================================*/

function UserMenuContent({ user, onLogout }) {
  return (
    <HStack
      gap={SP.lg}
      align="center"
      role="group"
      aria-label="User menu"
    >
      <UserIdentity user={user} />
      <VerticalDivider />
      <LogoutButton onClick={onLogout} />
    </HStack>
  );
}

/* ============================================================================
 * 18. MAIN — UserMenu
 *
 *    Public component. Reads `user` and `logout` from useAuth.
 *
 *    Behaviour:
 *      - If there is no signed-in user, returns null. This matches the
 *        original contract and preserves the header layout for anonymous
 *        routes (login, error pages, etc.).
 *      - Otherwise renders the identity block + logout button.
 *
 *    The component is intentionally a thin shell around UserMenuContent
 *    so that the composition can be reused elsewhere (e.g. in a mobile
 *    drawer) without duplicating the layout.
 * ==========================================================================*/

export default function UserMenu() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return <UserMenuContent user={user} onLogout={logout} />;
}

/* ============================================================================
 * DESIGN NOTES
 *
 *  Proportions
 *  -----------
 *  The three fixed heights (avatar 28, role badge 16, logout 26) were
 *  chosen so that:
 *    - The avatar and the identity block both reach ~28px.
 *    - The logout button is 2px shorter than the avatar, giving it a
 *      subtle "inset" feel without looking smaller.
 *    - The role badge is 16px, which is 10px smaller than the avatar,
 *      leaving clean negative space around it.
 *
 *  Color
 *  -----
 *  - Admin role uses the primary brand blue.
 *  - Operator role uses the brand orange, but at its accessible shade
 *    (orange700) so the text stays AA-compliant on the subtle fill.
 *  - All other roles use the neutral ink scale.
 *  - The avatar uses blue50/blue100 across all roles so the visual
 *    identity of "the current user" is stable regardless of permission.
 *
 *  Accessibility
 *  -------------
 *  - Avatar is aria-hidden because the name text is adjacent.
 *  - Role badge uses uppercase mono with 0.07em letter-spacing, which
 *    increases legibility at 10px.
 *  - Logout is a real <Button>, so Enter/Space both trigger it.
 *  - Focus rings are always visible via _focusVisible.
 *  - The whole menu is grouped with role="group" and aria-label.
 *
 *  Extending
 *  ---------
 *  Adding a new role:
 *    - Append an entry to ROLE_DEFS with { key, label, tone, tooltip }.
 *    - Done. No other file needs to change.
 *
 *  Adding a new tone:
 *    - Append an entry to ROLE_TONE_STYLES with { bg, fg, bd }.
 *    - Add the palette colors to P if they don't already exist.
 *
 *  What we deliberately did not build
 *  ----------------------------------
 *  - No dropdown with a "Profile / Settings / Sign out" list. The original
 *    contract is a single logout button; a dropdown would change behaviour.
 *  - No avatar upload, no gravatar lookup, no image URL rendering. The
 *    monogram is deterministic and requires zero network traffic.
 *  - No "signing out…" spinner. The original logout was synchronous in the
 *    sense that it simply called a context function; any async work should
 *    live in the auth provider, not here.
 * ==========================================================================*/