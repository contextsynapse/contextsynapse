/**
 * Vertical Registry — verticals declare their nav items here.
 *
 * Each vertical registers:
 * - id: unique identifier ("pms", "healthcare", etc.)
 * - name: display name
 * - navItems: sidebar navigation items
 * - roles: which roles see this vertical
 *
 * Admin sees ALL verticals grouped. FM sees only their vertical's items.
 */

// Icons are passed as references — import them where needed
const VERTICALS = {};

/**
 * Register a vertical's nav configuration.
 */
export function registerVertical(config) {
  VERTICALS[config.id] = config;
}

/**
 * Get all registered verticals.
 */
export function getVerticals() {
  return Object.values(VERTICALS);
}

/**
 * Get nav items for a specific role.
 * - Admin: all verticals grouped under their names
 * - FM/business: only their vertical's items (flat, no grouping)
 */
export function getNavForRole(role) {
  const isAdmin = !role || role === 'admin' || role === 'developer';
  const verticals = Object.values(VERTICALS);

  if (isAdmin) {
    // Group each vertical under its name
    return verticals.map(v => ({
      label: v.name,
      items: v.navItems,
    }));
  }

  // Business role: find their vertical, return items flat
  const userVertical = verticals.find(v =>
    v.roles.includes(role) || v.roles.includes('all')
  );

  if (userVertical) {
    return [{
      label: '',
      items: userVertical.navItems,
    }];
  }

  return [];
}

// ── Register PMS Vertical ──────────────────────────────────
// This would normally come from the plugin, but we register it here
// since the finance plugin's frontend pages are bundled in the main app.

export function registerPMS(icons) {
  registerVertical({
    id: 'pms',
    name: 'Portfolio Management',
    roles: ['fund_manager', 'compliance_officer', 'operations', 'all'],
    navItems: [
      { to: '/dashboard/portfolio', icon: icons.Briefcase, label: 'Portfolio' },
      { to: '/dashboard/command', icon: icons.Navigation, label: 'Trade' },
      { to: '/dashboard/compliance', icon: icons.Shield, label: 'Compliance' },
    ],
  });
}

// ── Example: How a healthcare vertical would register ────────
// registerVertical({
//   id: 'healthcare',
//   name: 'Healthcare',
//   roles: ['doctor', 'nurse', 'admin', 'all'],
//   navItems: [
//     { to: '/dashboard/patients', icon: Users, label: 'Patients' },
//     { to: '/dashboard/treatments', icon: Activity, label: 'Treatments' },
//     { to: '/dashboard/records', icon: FileText, label: 'Records' },
//   ],
// });
