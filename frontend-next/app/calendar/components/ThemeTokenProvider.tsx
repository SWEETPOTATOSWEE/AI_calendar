'use client';

import { useEffect, useState } from 'react';

type Oklch = { l: number; c: number; h: number };
type ModeColors = Record<string, Oklch>;

export default function ThemeTokenProvider() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    // 1. Initial theme detection (Tailwind class-based)
    const checkTheme = () => {
      const isDarkMode = document.documentElement.classList.contains('dark') || 
                         document.body.classList.contains('dark');
      setIsDark(isDarkMode);
    };

    checkTheme();

    // 2. Observer for theme changes (in case it toggles without page reload)
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'class') {
          checkTheme();
        }
      });
    });

    observer.observe(document.documentElement, { attributes: true });
    observer.observe(document.body, { attributes: true });

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const applyTokens = () => {
      const storageKey = isDark ? 'colors-dark' : 'colors-light';
      const saved = localStorage.getItem(storageKey);
      
      if (saved) {
        try {
          const colors: ModeColors = JSON.parse(saved);
          const root = document.documentElement;
          
          Object.entries(colors).forEach(([name, val]) => {
            // value format: 98.5% 0 0
            root.style.setProperty(`--${name}`, `${val.l}% ${val.c} ${val.h}`);
          });
        } catch (e) {
          console.error('Failed to apply design tokens:', e);
        }
      }
    };

    applyTokens();

    // 3. Listen for storage changes (if user edits in another tab)
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'colors-light' || e.key === 'colors-dark') {
        applyTokens();
      }
    };

    window.addEventListener('storage', handleStorageChange);
    
    // Also poll occasionally or use a custom event if colors are updated in the same tab
    // For now, let's assume storage event handles cross-tab and we might need a signal for same-tab
    const interval = setInterval(applyTokens, 2000); // Simple polling as fallback

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      clearInterval(interval);
    };
  }, [isDark]);

  return null; // Side-effect only component
}
