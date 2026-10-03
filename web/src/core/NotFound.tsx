import { Link, useLocation } from 'react-router-dom';
import { Compass } from 'lucide-react';

export default function NotFound() {
  const { pathname } = useLocation();
  return (
    <div className="max-w-xl mx-auto text-center py-24">
      <Compass className="w-10 h-10 mx-auto text-gray-600" />
      <h1 className="text-xl font-semibold text-white mt-4">No page at this address</h1>
      <p className="text-sm text-gray-400 mt-2 break-all"><code>{pathname}</code> is not a page in NexGen Transport.</p>
      <Link to="/" className="inline-block mt-6 text-sm text-blue-400 hover:text-blue-300">Go to the dashboard</Link>
    </div>
  );
}
