import React, { useState, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import { BiRefresh } from 'react-icons/bi';
import { useHapticFeedback } from '../hooks/useHapticFeedback';

const PullToRefresh = ({ onRefresh, children, threshold = 80, resistance = 0.5 }) => {
  const [isPulling, setIsPulling] = useState(false);
  const [pullDistance, setPullDistance] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [canRefresh, setCanRefresh] = useState(false);
  
  const startYRef = useRef(0);
  const containerRef = useRef(null);
  const { medium, success } = useHapticFeedback();

  const handleTouchStart = useCallback((e) => {
    if (isRefreshing) return;
    
    const touch = e.touches[0];
    const scrollTop = containerRef.current?.scrollTop || 0;
    
    // Only trigger pull-to-refresh at the top
    if (scrollTop === 0) {
      startYRef.current = touch.clientY;
      setIsPulling(true);
    }
  }, [isRefreshing]);

  const handleTouchMove = useCallback((e) => {
    if (!isPulling || isRefreshing) return;

    const touch = e.touches[0];
    const currentY = touch.clientY;
    const deltaY = (currentY - startYRef.current) * resistance;
    
    // Only allow pulling down
    if (deltaY > 0) {
      setPullDistance(Math.min(deltaY, threshold * 2));
      setCanRefresh(deltaY >= threshold);
      
      // Haptic feedback when reaching threshold
      if (deltaY >= threshold && !canRefresh) {
        medium();
      }
    }
  }, [isPulling, isRefreshing, threshold, resistance, canRefresh, medium]);

  const handleTouchEnd = useCallback(async () => {
    if (!isPulling) return;

    setIsPulling(false);

    if (canRefresh && onRefresh) {
      setIsRefreshing(true);
      setPullDistance(threshold);
      success();
      
      try {
        await onRefresh();
      } catch (error) {
        console.error('Refresh failed:', error);
      } finally {
        setIsRefreshing(false);
        setPullDistance(0);
        setCanRefresh(false);
      }
    } else {
      setPullDistance(0);
      setCanRefresh(false);
    }
  }, [isPulling, canRefresh, onRefresh, threshold, success]);

  const progress = Math.min(pullDistance / threshold, 1);

  return (
    <div
      ref={containerRef}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      className="relative overflow-hidden"
      style={{ touchAction: 'pan-y' }}
    >
      {/* Pull indicator */}
      <motion.div
        className="absolute top-0 left-0 right-0 flex items-center justify-center pointer-events-none z-50"
        initial={{ opacity: 0 }}
        animate={{ 
          opacity: isPulling || isRefreshing ? 1 : 0,
          y: isPulling ? pullDistance : isRefreshing ? threshold : -threshold,
        }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      >
        <div className="flex flex-col items-center gap-2">
          <motion.div
            animate={{ rotate: isRefreshing ? 360 : progress * 180 }}
            transition={{ 
              rotate: isRefreshing ? { duration: 1, repeat: Infinity, ease: 'linear' } : { type: 'spring', stiffness: 300, damping: 30 }
            }}
            className={`w-10 h-10 rounded-full flex items-center justify-center ${
              canRefresh || isRefreshing 
                ? 'bg-[var(--accent-primary)] text-white' 
                : 'bg-[var(--bg-surface)] text-[var(--text-muted)]'
            }`}
          >
            <BiRefresh className="text-xl" />
          </motion.div>
          <span className="text-xs text-[var(--text-muted)]">
            {isRefreshing ? 'Refreshing...' : canRefresh ? 'Release to refresh' : 'Pull to refresh'}
          </span>
        </div>
      </motion.div>

      {/* Content with offset during pull */}
      <motion.div
        animate={{ y: isPulling ? pullDistance : 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="relative"
      >
        {children}
      </motion.div>
    </div>
  );
};

export default PullToRefresh;
