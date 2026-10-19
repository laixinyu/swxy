import iconNewchat from '@/assets/layout/newchat.svg'
import iconRepository from '@/assets/layout/repository.svg'
import logo from '@/assets/logo.png'
import { deviceState } from '@/store/device'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import { Background } from './background'
import { Footer } from './footer'
import './index.scss'
import { Nav } from './nav'

const TITLE = import.meta.env.VITE_TITLE

export function BaseLayout({ children }: { children?: React.ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const device = useSnapshot(deviceState)

  function go(path: string) {
    if (device.chatting) {
      window.$app.message.info('回答生成中，请稍候再切换')
      return
    }
    navigate(path)
  }

  return (
    <div className="base-layout">
      <div className="base-layout__sidebar">
        <button className="base-layout__logo" type="button" onClick={() => go('/')}>
          <img className="logo" src={logo} alt="" />
          <span className="title">{TITLE}</span>
        </button>

        <div className="base-layout__sidebar-main scrollbar-style">
          <div className="base-layout__sidebar-main-content">
            <button
              className={`base-layout__nav-header${location.pathname === '/' ? ' is-active' : ''}`}
              type="button"
              onClick={() => go('/')}
            >
              <img className="base-layout__nav-header-icon" src={iconNewchat} alt="" />
              <span className="base-layout__nav-header-title">新对话</span>
            </button>

            <Nav />

            <button
              className={`base-layout__nav-header${location.pathname === '/repository' ? ' is-active' : ''}`}
              type="button"
              aria-current={location.pathname === '/repository' ? 'page' : undefined}
              onClick={() => go('/repository')}
            >
              <img className="base-layout__nav-header-icon" src={iconRepository} alt="" />
              <span className="base-layout__nav-header-title">知识库</span>
            </button>
          </div>
        </div>

        <Footer />
      </div>

      <div className="base-layout__content">{children}</div>

      <Background />
    </div>
  )
}
