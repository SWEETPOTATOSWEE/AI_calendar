'use client';

import { useState, useEffect } from 'react';

type ColorItem = {
  name: string;
};

type ColorCategory = {
  categoryName?: string;
  items: ColorItem[];
};

type Section = {
  id: number;
  title: string;
  categories: ColorCategory[];
};

const SECTIONS: Section[] = [
  {
    id: 1,
    title: 'Neutral',
    categories: [
      {
        categoryName: 'Surface',
        items: [
          { name: 'bg-canvas' },
          { name: 'bg-surface' },
          { name: 'bg-subtle' },
        ],
      },
      {
        categoryName: 'Typography',
        items: [
          { name: 'text-primary' },
          { name: 'text-secondary' },
          { name: 'text-disabled' },
        ],
      },
      {
        categoryName: 'Border',
        items: [
          { name: 'border-subtle' },
          { name: 'border-strong' },
        ],
      },
    ],
  },
  {
    id: 2,
    title: 'Brand/Primary',
    categories: [
      {
        items: [
          { name: 'primary' },
          { name: 'primary-hover' },
          { name: 'primary-low' },
        ],
      },
    ],
  },
  {
    id: 3,
    title: 'Semantic/Status',
    categories: [
      {
        items: [
          { name: 'success' },
          { name: 'error' },
          { name: 'warning' },
          { name: 'info' },
        ],
      },
    ],
  },
  {
    id: 4,
    title: 'Interaction',
    categories: [
      {
        items: [
          { name: 'overlay' },
          { name: 'focus' },
        ],
      },
    ],
  },
];

// Initial state for all colors
type OKLCH = { l: number; c: number; h: number };
type ModeColors = Record<string, OKLCH>;

const createInitialModeColors = (light: boolean): ModeColors => {
  if (light) {
    return {
      'bg-canvas': { l: 98.5, c: 0, h: 0 },
      'bg-surface': { l: 96.5, c: 0, h: 0 },
      'bg-subtle': { l: 93.5, c: 0.008, h: 250 },
      'text-primary': { l: 22, c: 0.03, h: 250 },
      'text-secondary': { l: 42, c: 0.02, h: 250 },
      'text-disabled': { l: 62, c: 0.015, h: 250 },
      'border-subtle': { l: 86, c: 0.01, h: 250 },
      'border-strong': { l: 72, c: 0.015, h: 250 },
      'primary': { l: 55, c: 0.18, h: 255 },
      'primary-hover': { l: 50, c: 0.18, h: 255 },
      'primary-low': { l: 92, c: 0.05, h: 255 },
      'success': { l: 62, c: 0.16, h: 145 },
      'error': { l: 60, c: 0.2, h: 25 },
      'warning': { l: 75, c: 0.16, h: 85 },
      'info': { l: 62, c: 0.13, h: 240 },
      'overlay': { l: 20, c: 0, h: 0 },
      'focus': { l: 65, c: 0.2, h: 255 },
    };
  } else {
    return {
      'bg-canvas': { l: 14, c: 0.01, h: 250 },
      'bg-surface': { l: 18, c: 0.01, h: 250 },
      'bg-subtle': { l: 22, c: 0.015, h: 250 },
      'text-primary': { l: 95, c: 0.01, h: 250 },
      'text-secondary': { l: 78, c: 0.01, h: 250 },
      'text-disabled': { l: 60, c: 0.01, h: 250 },
      'border-subtle': { l: 30, c: 0.015, h: 250 },
      'border-strong': { l: 42, c: 0.015, h: 250 },
      'primary': { l: 68, c: 0.18, h: 255 },
      'primary-hover': { l: 72, c: 0.18, h: 255 },
      'primary-low': { l: 30, c: 0.08, h: 255 },
      'success': { l: 70, c: 0.16, h: 145 },
      'error': { l: 68, c: 0.2, h: 25 },
      'warning': { l: 80, c: 0.16, h: 85 },
      'info': { l: 72, c: 0.13, h: 240 },
      'overlay': { l: 5, c: 0, h: 0 },
      'focus': { l: 80, c: 0.2, h: 255 },
    };
  }
};

export default function ColorsPage() {
  const [activeTab, setActiveTab] = useState(1);
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [lightColors, setLightColors] = useState<ModeColors>(() => createInitialModeColors(true));
  const [darkColors, setDarkColors] = useState<ModeColors>(() => createInitialModeColors(false));
  const [editingColor, setEditingColor] = useState<string | null>(null);

  // Load colors from localStorage on mount
  useEffect(() => {
    const savedLight = localStorage.getItem('colors-light');
    const savedDark = localStorage.getItem('colors-dark');
    if (savedLight) {
      try {
        setLightColors(JSON.parse(savedLight));
      } catch (e) {
        console.error('Failed to parse light colors', e);
      }
    }
    if (savedDark) {
      try {
        setDarkColors(JSON.parse(savedDark));
      } catch (e) {
        console.error('Failed to parse dark colors', e);
      }
    }
  }, []);

  // Save colors to localStorage whenever they change
  useEffect(() => {
    if (Object.keys(lightColors).length > 0) {
      localStorage.setItem('colors-light', JSON.stringify(lightColors));
    }
  }, [lightColors]);

  useEffect(() => {
    if (Object.keys(darkColors).length > 0) {
      localStorage.setItem('colors-dark', JSON.stringify(darkColors));
    }
  }, [darkColors]);

  const activeSection = SECTIONS.find((s) => s.id === activeTab);
  const currentColors = isDarkMode ? darkColors : lightColors;
  const setModeColors = isDarkMode ? setDarkColors : setLightColors;

  const resetToDefaults = () => {
    if (confirm('모든 색상을 초기 기본값으로 재설정하시겠습니까?')) {
      const initialLight = createInitialModeColors(true);
      const initialDark = createInitialModeColors(false);
      setLightColors(initialLight);
      setDarkColors(initialDark);
      localStorage.setItem('colors-light', JSON.stringify(initialLight));
      localStorage.setItem('colors-dark', JSON.stringify(initialDark));
    }
  };

  const handleOklchChange = (name: string, key: 'l' | 'c' | 'h', value: number) => {
    if (isNaN(value)) return;
    setModeColors(prev => {
      const currentColor = prev[name] || { l: isDarkMode ? 0 : 100, c: 0, h: 0 };
      return {
        ...prev,
        [name]: { ...currentColor, [key]: value }
      };
    });
  };

  return (
    <div className={`p-8 min-h-screen transition-colors duration-300 ${isDarkMode ? 'bg-gray-900 text-gray-100' : 'bg-white text-gray-900'}`}>
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-6 mb-8">
          <h1 className="text-2xl font-bold">Colors Overview</h1>
          <button
            onClick={resetToDefaults}
            className={`px-3 py-1 text-xs font-medium rounded border transition-colors ${
              isDarkMode 
                ? 'bg-gray-800 text-gray-300 border-gray-700 hover:bg-gray-700' 
                : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
            }`}
          >
            기본값 초기화
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setIsDarkMode(!isDarkMode)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                isDarkMode ? 'bg-blue-600' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle dark mode</span>
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  isDarkMode ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
            <span className="text-sm font-medium">{isDarkMode ? 'Dark Mode' : 'Light Mode'}</span>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-4 mb-8">
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              onClick={() => {
                setActiveTab(section.id);
                setEditingColor(null);
              }}
              className={`px-4 py-2 rounded-md border transition-colors ${
                activeTab === section.id
                  ? 'bg-blue-600 text-white border-blue-600'
                  : isDarkMode 
                    ? 'bg-gray-800 text-gray-300 border-gray-700 hover:bg-gray-700'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
              }`}
            >
              {section.title}
            </button>
          ))}
        </div>

        <div className="flex gap-12">
          {/* Color List */}
          <div className="flex-1 space-y-8">
            <h2 className="text-xl font-semibold border-b pb-2">
              {activeSection?.title}
            </h2>

            {activeSection?.categories.map((category, catIdx) => (
              <div key={catIdx} className="space-y-4">
                {category.categoryName && (
                  <h3 className={`text-lg font-medium ${isDarkMode ? 'text-gray-300' : 'text-gray-800'}`}>
                    {category.categoryName}
                  </h3>
                )}
                <div className="space-y-3">
                  {category.items.map((item, itemIdx) => {
                    const color = currentColors[item.name] || { l: isDarkMode ? 0 : 100, c: 0, h: 0 };
                    const oklchStr = `${color.l}% ${color.c} ${color.h}`;
                    return (
                      <div key={itemIdx} className="flex items-center gap-4">
                        <span className={`w-40 text-sm ${isDarkMode ? 'text-gray-400' : 'text-gray-600'}`}>{item.name}</span>
                        <button
                          onClick={() => setEditingColor(item.name)}
                          className={`w-24 h-8 border transition-all ${
                            editingColor === item.name ? 'ring-2 ring-blue-500 border-blue-500' : isDarkMode ? 'border-gray-700' : 'border-gray-300'
                          }`}
                          style={{ backgroundColor: `oklch(${oklchStr})` }}
                          title="Click to edit"
                        ></button>
                        <span className={`text-sm font-mono ${isDarkMode ? 'text-gray-500' : 'text-gray-500'}`}>oklch({oklchStr})</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Editor Panel */}
          {editingColor && (
            <div className={`w-64 p-4 border rounded-lg h-fit sticky top-8 ${isDarkMode ? 'bg-gray-800 border-gray-700' : 'bg-gray-50 border-gray-200'}`}>
              <h3 className={`font-bold mb-4 border-b pb-2 truncate ${isDarkMode ? 'text-white border-gray-700' : 'text-gray-900 border-gray-200'}`}>
                Editing: {editingColor}
              </h3>
              <div className="space-y-6">
                <div>
                  <div className="flex justify-between mb-1 items-center">
                    <label className={`text-xs font-semibold ${isDarkMode ? 'text-gray-400' : 'text-gray-600'}`}>Lightness (L)</label>
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="0.1"
                        value={currentColors[editingColor]?.l ?? (isDarkMode ? 0 : 100)}
                        onChange={(e) => handleOklchChange(editingColor, 'l', Math.min(100, Math.max(0, parseFloat(e.target.value) || 0)))}
                        className={`text-xs w-16 border rounded px-1 py-0.5 text-right font-mono ${isDarkMode ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-300'}`}
                      />
                      <span className="text-xs text-gray-500">%</span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="0.1"
                    value={currentColors[editingColor]?.l ?? (isDarkMode ? 0 : 100)}
                    onChange={(e) => handleOklchChange(editingColor, 'l', parseFloat(e.target.value))}
                    className={`w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-600 ${isDarkMode ? 'bg-gray-700' : 'bg-gray-200'}`}
                  />
                </div>
                <div>
                  <div className="flex justify-between mb-1 items-center">
                    <label className={`text-xs font-semibold ${isDarkMode ? 'text-gray-400' : 'text-gray-600'}`}>Chroma (C)</label>
                    <input
                      type="number"
                      min="0"
                      max="0.4"
                      step="0.001"
                      value={currentColors[editingColor]?.c ?? 0}
                      onChange={(e) => handleOklchChange(editingColor, 'c', Math.min(0.4, Math.max(0, parseFloat(e.target.value) || 0)))}
                      className={`text-xs w-16 border rounded px-1 py-0.5 text-right font-mono ${isDarkMode ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-300'}`}
                    />
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="0.4"
                    step="0.001"
                    value={currentColors[editingColor]?.c ?? 0}
                    onChange={(e) => handleOklchChange(editingColor, 'c', parseFloat(e.target.value))}
                    className={`w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-600 ${isDarkMode ? 'bg-gray-700' : 'bg-gray-200'}`}
                  />
                </div>
                <div>
                  <style jsx>{`
                    .hue-range {
                      background: linear-gradient(
                        to right,
                        oklch(70% 0.15 0),
                        oklch(70% 0.15 60),
                        oklch(70% 0.15 120),
                        oklch(70% 0.15 180),
                        oklch(70% 0.15 240),
                        oklch(70% 0.15 300),
                        oklch(70% 0.15 360)
                      );
                      appearance: none;
                      height: 8px;
                      border-radius: 4px;
                    }
                    .hue-range::-webkit-slider-thumb {
                      appearance: none;
                      width: 16px;
                      height: 16px;
                      background: white;
                      border: 2px solid #555;
                      border-radius: 50%;
                      cursor: pointer;
                    }
                  `}</style>
                  <div className="flex justify-between mb-1 items-center">
                    <label className={`text-xs font-semibold ${isDarkMode ? 'text-gray-400' : 'text-gray-600'}`}>Hue (H)</label>
                    <input
                      type="number"
                      min="0"
                      max="360"
                      value={currentColors[editingColor]?.h ?? 0}
                      onChange={(e) => handleOklchChange(editingColor, 'h', Math.min(360, Math.max(0, parseInt(e.target.value) || 0)))}
                      className={`text-xs w-16 border rounded px-1 py-0.5 text-right font-mono ${isDarkMode ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-300'}`}
                    />
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="360"
                    value={currentColors[editingColor]?.h ?? 0}
                    onChange={(e) => handleOklchChange(editingColor, 'h', parseInt(e.target.value))}
                    className="w-full h-2 rounded-lg appearance-none cursor-pointer hue-range"
                  />
                </div>
              </div>
              <button
                onClick={() => setEditingColor(null)}
                className="mt-6 w-full text-xs text-gray-500 hover:text-gray-400 transition-colors"
              >
                Close Editor
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
