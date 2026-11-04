import { useRef, useState, useEffect } from 'react';
import type { RefObject } from 'react';

/**
 * Mirrors Ariakit's composite `data-active-item` attribute into a React state value.
 * The ref must be attached to an element that mounts synchronously on first render;
 * late-mounting refs will not be observed.
 */
export default function useIsActiveItem<T extends HTMLElement = HTMLElement>(): {
  ref: RefObject<T>;
  isActive: boolean;
} {
  const ref = useRef<T>(null);
  const [isActive, setIsActive] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }

    const observer = new MutationObserver(() => {
      // Re-read the DOM as the source of truth. This also handles DOM implementations
      // that omit the changed attribute name on removal records.
      setIsActive(element.hasAttribute('data-active-item'));
    });

    // Observe all attributes and filter the records ourselves. Some DOM implementations
    // used in CI do not consistently deliver filtered MutationObserver records for removals.
    observer.observe(element, { attributes: true });
    setIsActive(element.hasAttribute('data-active-item'));

    return () => observer.disconnect();
  }, []);

  return { ref, isActive };
}
