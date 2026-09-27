import { useRef, type ReactNode } from 'react';
import './SpotlightCard.css';

interface SpotlightCardProps {
  children: ReactNode;
  className?: string;
  spotlightColor?: string;
}

const SpotlightCard = ({
  children,
  className = '',
  spotlightColor = 'rgba(0, 240, 255, 0.14)'
}: SpotlightCardProps) => {
  const divRef = useRef<HTMLDivElement>(null);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const div = divRef.current;
    if (!div) return;
    const rect = div.getBoundingClientRect();
    div.style.setProperty('--mouse-x', `${e.clientX - rect.left}px`);
    div.style.setProperty('--mouse-y', `${e.clientY - rect.top}px`);
    div.style.setProperty('--spotlight-color', spotlightColor);
  };

  return (
    <div ref={divRef} onMouseMove={handleMouseMove} className={`card-spotlight ${className}`}>
      {children}
    </div>
  );
};

export default SpotlightCard;
