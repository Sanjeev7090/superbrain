/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
        extend: {
                /* ── iOS Human Interface Guidelines Typography Scale ──────────────
                   Large Title   : 34pt  → text-4xl / text-5xl / text-6xl
                   Section Header: 22–28pt → text-2xl / text-3xl
                   Body Primary  : 17pt  → text-base / text-lg
                   Body Secondary: 15pt  → text-sm          (Subhead)
                   Captions/Hints: 11–12pt → text-xs / text-caption
                   Button Text   : 17pt Semibold → text-base font-semibold
                ──────────────────────────────────────────────────────────────── */
                fontSize: {
                    /* Caption 2 — 11pt */
                    'xs':      ['11px', { lineHeight: '13px',  letterSpacing: '0.012em'  }],
                    /* Caption 1 — 12pt */
                    'caption': ['12px', { lineHeight: '16px',  letterSpacing: '0.006em'  }],
                    /* Body Secondary / Subhead — 15pt */
                    'sm':      ['15px', { lineHeight: '20px',  letterSpacing: '-0.008em' }],
                    /* Body Primary / Button — 17pt */
                    'base':    ['17px', { lineHeight: '22px',  letterSpacing: '-0.015em' }],
                    /* Button alias — 17pt Semibold (caller adds font-semibold) */
                    'lg':      ['17px', { lineHeight: '22px',  letterSpacing: '-0.015em' }],
                    /* Title 3 — 20pt */
                    'xl':      ['20px', { lineHeight: '25px',  letterSpacing: '-0.02em'  }],
                    /* Section Header lower — 22pt */
                    '2xl':     ['22px', { lineHeight: '28px',  letterSpacing: '-0.025em' }],
                    /* Section Header upper — 28pt */
                    '3xl':     ['28px', { lineHeight: '34px',  letterSpacing: '-0.03em'  }],
                    /* Large Title — 34pt */
                    '4xl':     ['34px', { lineHeight: '41px',  letterSpacing: '-0.035em' }],
                    /* Large Title cap — 34pt (5xl / 6xl capped same) */
                    '5xl':     ['34px', { lineHeight: '41px',  letterSpacing: '-0.035em' }],
                    '6xl':     ['34px', { lineHeight: '41px',  letterSpacing: '-0.035em' }],
                },
                borderRadius: {
                        lg: 'var(--radius)',
                        md: 'calc(var(--radius) - 2px)',
                        sm: 'calc(var(--radius) - 4px)'
                },
                colors: {
                        background: 'hsl(var(--background))',
                        foreground: 'hsl(var(--foreground))',
                        card: {
                                DEFAULT: 'hsl(var(--card))',
                                foreground: 'hsl(var(--card-foreground))'
                        },
                        popover: {
                                DEFAULT: 'hsl(var(--popover))',
                                foreground: 'hsl(var(--popover-foreground))'
                        },
                        primary: {
                                DEFAULT: 'hsl(var(--primary))',
                                foreground: 'hsl(var(--primary-foreground))'
                        },
                        secondary: {
                                DEFAULT: 'hsl(var(--secondary))',
                                foreground: 'hsl(var(--secondary-foreground))'
                        },
                        muted: {
                                DEFAULT: 'hsl(var(--muted))',
                                foreground: 'hsl(var(--muted-foreground))'
                        },
                        accent: {
                                DEFAULT: 'hsl(var(--accent))',
                                foreground: 'hsl(var(--accent-foreground))'
                        },
                        destructive: {
                                DEFAULT: 'hsl(var(--destructive))',
                                foreground: 'hsl(var(--destructive-foreground))'
                        },
                        border: 'hsl(var(--border))',
                        input: 'hsl(var(--input))',
                        ring: 'hsl(var(--ring))',
                        chart: {
                                '1': 'hsl(var(--chart-1))',
                                '2': 'hsl(var(--chart-2))',
                                '3': 'hsl(var(--chart-3))',
                                '4': 'hsl(var(--chart-4))',
                                '5': 'hsl(var(--chart-5))'
                        }
                },
                keyframes: {
                        'accordion-down': {
                                from: { height: '0' },
                                to:   { height: 'var(--radix-accordion-content-height)' }
                        },
                        'accordion-up': {
                                from: { height: 'var(--radix-accordion-content-height)' },
                                to:   { height: '0' }
                        }
                },
                animation: {
                        'accordion-down': 'accordion-down 0.2s ease-out',
                        'accordion-up':   'accordion-up 0.2s ease-out'
                }
        }
  },
  plugins: [require("tailwindcss-animate")],
};
