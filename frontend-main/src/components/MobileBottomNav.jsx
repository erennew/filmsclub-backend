import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { BiHomeAlt2, BiSolidMovie } from 'react-icons/bi';
import { BsTv, BsFire } from 'react-icons/bs';
import { MdOutlineLiveTv } from 'react-icons/md';
import { useHapticFeedback } from '../hooks/useHapticFeedback';

const navItems = [
  { id: 'home', label: 'Home', icon: BiHomeAlt2, path: '/' },
  { id: 'movies', label: 'Movies', icon: BiSolidMovie, path: '/Movies' },
  { id: 'series', label: 'Series', icon: BsTv, path: '/Series' },
  { id: 'anime', label: 'Anime', icon: BsFire, path: '/anime' },
  { id: 'kdrama', label: 'K-Drama', icon: MdOutlineLiveTv, path: '/k-drama' },
];

const MobileBottomNav = () => {
  const location = useLocation();
  const [activeTab, setActiveTab] = useState('home');
  const { navigation } = useHapticFeedback();

  const handleTabChange = (id, path) => {
    setActiveTab(id);
    navigation();
  };

  return (
    <motion.nav
      initial={{ y: 100 }}
      animate={{ y: 0 }}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-black/95 backdrop-blur-lg border-t border-gray-800"
      style={{
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <div className="flex items-center justify-around px-2 py-3">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path || (item.id === 'home' && location.pathname === '/');
          const Icon = item.icon;

          return (
            <Link
              key={item.id}
              to={item.path}
              onClick={() => handleTabChange(item.id, item.path)}
              className="relative flex flex-col items-center justify-center min-w-[44px] min-h-[44px] px-3 py-2 transition-all duration-200"
            >
              {/* Icon */}
              <motion.div
                animate={{ scale: isActive ? 1.05 : 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 25 }}
                className="relative"
              >
                <Icon 
                  className={`text-2xl ${isActive ? 'text-[#E50914]' : 'text-gray-400'}`}
                />
              </motion.div>

              {/* Label */}
              <span className={`text-[10px] font-medium mt-1 ${isActive ? 'text-white' : 'text-gray-500'}`}>
                {item.label}
              </span>

              {/* Active indicator line */}
              {isActive && (
                <motion.div
                  layoutId="activeTab"
                  className="absolute -top-1 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-[#E50914] rounded-full"
                  transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                />
              )}
            </Link>
          );
        })}
      </div>
    </motion.nav>
  );
};

export default MobileBottomNav;
