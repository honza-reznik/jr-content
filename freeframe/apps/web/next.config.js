/**
 * Mount the app under a sub-path (e.g. /freeframe) instead of the domain root.
 * Build-time: set NEXT_PUBLIC_BASE_PATH and rebuild the web image. Empty by
 * default, so root deployments are unchanged.
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''
if (basePath && !/^(\/[A-Za-z0-9_-]+)+$/.test(basePath)) {
  throw new Error('NEXT_PUBLIC_BASE_PATH must be empty or a path without trailing slash')
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  output: process.env.JRC_MODE ? undefined : 'standalone',
  eslint: {
    ignoreDuringBuilds: true,
  },
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '9000',
      },
    ],
  },
}

module.exports = nextConfig
