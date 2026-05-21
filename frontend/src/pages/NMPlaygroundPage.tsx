/**
 * @file_name: NMPlaygroundPage.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Visual playground / gallery showing every NM primitive in
 * light + dark side-by-side. Internal dev-mode page for human visual review;
 * not linked from any production navigation.
 *
 * Mount at /app/nm-playground (or wherever the route table allows) and
 * walk through to verify NM tokens render correctly across every primitive.
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §7.1
 */

import { useState, type ReactNode } from 'react';
import {
  RingAvatar,
  GroupAvatar,
  SpeciesDot,
  AvatarStack,
  AvatarWithStatus,
  BracketMarkLogo,
  BracketSectionLabel,
  BracketCornerMarks,
  BracketEmptyState,
  BracketDropzone,
  BracketLoading,
  PaperCard,
  RaisedPanel,
  SunkenWell,
  Divider,
  MessageBubble,
  BubbleGroup,
  BubbleMetaRow,
  TurnBreak,
  Button,
  IconButton,
  ButtonGroup,
  SplitButton,
  StatusDot,
  StatusBadge,
  ConnectionBanner,
  Toast,
  Skeleton,
  Spinner,
  ProgressBar,
  TextInput,
  Textarea,
  Select,
  Toggle,
  Checkbox,
  Radio,
  Slider,
  SearchInput,
  FormField,
  TabBar,
  SidebarNavItem,
  Breadcrumb,
  StepIndicator,
  BottomNavBar,
  Dialog,
  ConfirmDialog,
  Drawer,
  Sheet,
  KPITile,
  StatStrip,
  ChartCard,
  Chip,
  Tag,
  Badge,
  CodeBlock,
  Kbd,
  Link,
} from '../components/nm';

function ThemePane({ theme, children }: { theme: 'light' | 'dark'; children: ReactNode }) {
  return (
    <div
      className={theme === 'dark' ? 'dark' : ''}
      style={{
        background: 'var(--nm-paper)',
        color: 'var(--nm-ink)',
        padding: 24,
        borderRadius: 14,
        border: '1px solid var(--nm-hairline)',
      }}
    >
      <div
        className="text-[11px] uppercase tracking-[0.12em] mb-3"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
      >
        [ {theme} ]
      </div>
      {children}
    </div>
  );
}

function Section({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <section id={id} style={{ marginBottom: 48 }}>
      <h2
        style={{
          fontFamily: 'var(--font-display)',
          fontSize: 22,
          fontWeight: 700,
          letterSpacing: '-0.02em',
          color: 'var(--nm-ink)',
          marginBottom: 4,
        }}
      >
        {label}
      </h2>
      <Divider />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 12 }}>
        <ThemePane theme="light">{children}</ThemePane>
        <ThemePane theme="dark">{children}</ThemePane>
      </div>
    </section>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3 mb-3">{children}</div>;
}

export default function NMPlaygroundPage() {
  const [text, setText] = useState('');
  const [toggle, setToggle] = useState(true);
  const [check, setCheck] = useState(false);
  const [radio, setRadio] = useState('a');
  const [slider, setSlider] = useState(60);
  const [selectVal, setSelectVal] = useState('a');
  const [tab, setTab] = useState('all');
  const [bottom, setBottom] = useState('chats');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [bannerState, setBannerState] = useState<'synced' | 'connecting' | 'sync-error' | 'offline'>('connecting');

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--bg-deep)',
        padding: 32,
        fontFamily: 'var(--font-sans)',
      }}
    >
      <header style={{ marginBottom: 40 }}>
        <BracketMarkLogo />
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: 32,
            fontWeight: 700,
            letterSpacing: '-0.025em',
            color: 'var(--nm-ink)',
            marginTop: 16,
          }}
        >
          NM Design System Playground
        </h1>
        <p style={{ color: 'var(--nm-ink70)', marginTop: 8 }}>
          Every primitive in light + dark side-by-side. Internal dev page. 2026-05-18.
        </p>
      </header>

      <Section id="identity" label="Identity">
        <Row>
          <RingAvatar species="carbon" label="J" size="xs" />
          <RingAvatar species="carbon" label="J" size="sm" />
          <RingAvatar species="silicon" label="G" />
          <RingAvatar species="overlap" label="M" size="lg" />
          <RingAvatar species="silicon" label="Yara" size="xl" />
        </Row>
        <Row>
          <GroupAvatar members={[{ species: 'carbon' }, { species: 'carbon' }, { species: 'carbon' }, { species: 'silicon' }]} />
          <GroupAvatar members={[{ species: 'carbon' }, { species: 'silicon' }]} />
          <GroupAvatar members={[{ species: 'silicon' }, { species: 'silicon' }, { species: 'silicon' }]} />
        </Row>
        <Row>
          <SpeciesDot species="carbon" />
          <SpeciesDot species="silicon" filled={false} />
          <SpeciesDot species="overlap" pulse />
        </Row>
        <Row>
          <AvatarStack
            avatars={[
              { species: 'carbon', label: 'A' },
              { species: 'silicon', label: 'B' },
              { species: 'carbon', label: 'C' },
              { species: 'overlap', label: 'D' },
              { species: 'silicon', label: 'E' },
            ]}
          />
          <AvatarWithStatus status="success">
            <RingAvatar species="silicon" label="Y" />
          </AvatarWithStatus>
          <AvatarWithStatus status="error">
            <RingAvatar species="carbon" label="X" />
          </AvatarWithStatus>
        </Row>
      </Section>

      <Section id="bracket" label="Bracket Vocabulary">
        <Row><BracketMarkLogo /></Row>
        <Row><BracketMarkLogo showWordmark={false} size={32} /></Row>
        <BracketSectionLabel>Active Agents</BracketSectionLabel>
        <BracketSectionLabel trailing={<span style={{ color: 'var(--nm-ink30)' }}>12</span>}>People</BracketSectionLabel>
        <div className="mt-4">
          <BracketCornerMarks>
            <PaperCard padding="md">selected card</PaperCard>
          </BracketCornerMarks>
        </div>
        <div className="mt-4">
          <BracketEmptyState label="No conversations yet" hint="Start a new chat to see it here." cta={<Button>+ New chat</Button>} />
        </div>
        <div className="mt-4">
          <BracketDropzone>
            <div style={{ marginBottom: 8 }}>Drop bundle file here</div>
            <Button variant="secondary" size="sm">Browse</Button>
          </BracketDropzone>
        </div>
        <div className="mt-4">
          <BracketLoading label="Loading" />
        </div>
      </Section>

      <Section id="surface" label="Surface">
        <Row>
          <PaperCard>PaperCard</PaperCard>
          <RaisedPanel>RaisedPanel</RaisedPanel>
          <SunkenWell>SunkenWell</SunkenWell>
        </Row>
        <Divider />
        <Divider variant="thick" />
      </Section>

      <Section id="bubble" label="Bubbles">
        <BubbleGroup>
          <BubbleMetaRow sender="Jane" species="carbon" time="12:04" />
          <MessageBubble variant="human-other">下午有空吗？</MessageBubble>
          <BubbleMetaRow sender="Genie" species="silicon" time="12:05" />
          <MessageBubble variant="ai-other">你 3 点之后空着，要帮你约一下吗？</MessageBubble>
          <TurnBreak />
          <BubbleMetaRow sender="You" species="neutral" time="12:05" alignRight />
          <MessageBubble variant="own">麻烦你了</MessageBubble>
          <TurnBreak />
          <MessageBubble variant="own-lilac">co-write reply</MessageBubble>
          <MessageBubble variant="system">Jane joined</MessageBubble>
          <MessageBubble variant="tool-result">{`> search_files("carbon")\n→ 3 results, 412ms`}</MessageBubble>
          <MessageBubble variant="error">Tool timed out · Retry</MessageBubble>
        </BubbleGroup>
      </Section>

      <Section id="button" label="Buttons">
        <Row>
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="link">Link</Button>
        </Row>
        <Row>
          <Button size="sm">Small</Button>
          <Button>Medium</Button>
          <Button size="lg">Large</Button>
          <Button loading>Saving</Button>
        </Row>
        <Row>
          <IconButton label="Add">
            <svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" strokeWidth="1.5" fill="none"><path d="M7 2 V12 M2 7 H12" strokeLinecap="round" /></svg>
          </IconButton>
          <ButtonGroup>
            <Button variant="secondary">A</Button>
            <Button variant="secondary">B</Button>
            <Button variant="secondary">C</Button>
          </ButtonGroup>
          <SplitButton onPrimaryClick={() => {}} onDropdownClick={() => {}}>Deploy</SplitButton>
        </Row>
      </Section>

      <Section id="status" label="Status & Connection">
        <Row>
          <StatusDot status="success" /> <span className="text-xs">success</span>
          <StatusDot status="warning" /> <span className="text-xs">warning</span>
          <StatusDot status="error" /> <span className="text-xs">error</span>
          <StatusDot status="info" /> <span className="text-xs">info</span>
          <StatusDot status="success" pulse /> <span className="text-xs">pulse</span>
        </Row>
        <Row>
          <StatusBadge status="success">ONLINE</StatusBadge>
          <StatusBadge status="warning">THROTTLED</StatusBadge>
          <StatusBadge status="error">FAILED</StatusBadge>
        </Row>
        <div className="space-y-1 mb-3">
          <ConnectionBanner state="connecting" />
          <ConnectionBanner state="sync-error" onRetry={() => {}} />
          <ConnectionBanner state="offline" />
          <ConnectionBanner state={bannerState} />
        </div>
        <Row>
          {(['synced', 'connecting', 'sync-error', 'offline'] as const).map((s) => (
            <Button key={s} variant="ghost" size="sm" onClick={() => setBannerState(s)}>
              {s}
            </Button>
          ))}
        </Row>
        <Toast title="Agent done" description="Yara finished daily summary." status="success" onDismiss={() => {}} />
      </Section>

      <Section id="feedback" label="Feedback">
        <Row>
          <Skeleton width={120} height={20} />
          <Skeleton variant="text" lines={3} />
          <Skeleton variant="circle" />
        </Row>
        <Row>
          <Spinner />
          <Spinner species="carbon" />
          <Spinner species="silicon" />
        </Row>
        <ProgressBar value={62} label="Uploading" showPercent />
      </Section>

      <Section id="form" label="Forms">
        <FormField label="Name" hint="What's your name?">
          <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Jane" />
        </FormField>
        <div className="mt-3" />
        <FormField label="Bio">
          <Textarea placeholder="Tell us…" />
        </FormField>
        <div className="mt-3" />
        <FormField label="Model">
          <Select
            value={selectVal}
            onChange={(e) => setSelectVal(e.target.value)}
            options={[
              { value: 'a', label: 'Claude Sonnet 4.6' },
              { value: 'b', label: 'GPT-4' },
              { value: 'c', label: 'DeepSeek V4' },
            ]}
          />
        </FormField>
        <div className="mt-3 flex flex-col gap-3">
          <Toggle checked={toggle} onChange={setToggle} label="Compact mode" />
          <Checkbox checked={check} onChange={setCheck} label="I agree" />
          <div className="flex gap-3">
            <Radio checked={radio === 'a'} onChange={() => setRadio('a')} label="Light" />
            <Radio checked={radio === 'b'} onChange={() => setRadio('b')} label="Dark" />
            <Radio checked={radio === 'c'} onChange={() => setRadio('c')} label="Auto" />
          </div>
          <Slider value={slider} onChange={setSlider} label="Opacity" unit="%" />
          <SearchInput value={text} onChange={setText} />
        </div>
      </Section>

      <Section id="nav" label="Navigation">
        <TabBar
          tabs={[
            { key: 'all', label: 'All' },
            { key: 'active', label: 'Active', count: 4 },
            { key: 'paused', label: 'Paused' },
          ]}
          active={tab}
          onChange={setTab}
        />
        <div className="mt-3 max-w-[280px]">
          <SidebarNavItem active>Dashboard</SidebarNavItem>
          <SidebarNavItem>Agents</SidebarNavItem>
          <SidebarNavItem>Settings</SidebarNavItem>
        </div>
        <Breadcrumb
          className="mt-3"
          items={[
            { label: 'Home', href: '/' },
            { label: 'Agents', href: '/agents' },
            { label: 'Yara' },
          ]}
        />
        <div className="mt-4">
          <StepIndicator
            steps={[
              { key: '1', label: 'Welcome' },
              { key: '2', label: 'Identity' },
              { key: '3', label: 'Modules' },
              { key: '4', label: 'Connect' },
              { key: '5', label: 'Done' },
            ]}
            currentIndex={1}
          />
        </div>
        <div className="mt-4">
          <BottomNavBar
            tabs={[
              { key: 'chats', label: 'Chats', icon: '💬' },
              { key: 'people', label: 'People', icon: '👥' },
              { key: 'me', label: 'Me', icon: '👤' },
            ]}
            active={bottom}
            onChange={setBottom}
          />
        </div>
      </Section>

      <Section id="modal" label="Modal">
        <Row>
          <Button onClick={() => setDialogOpen(true)}>Open Dialog</Button>
          <Button onClick={() => setConfirmOpen(true)} variant="danger">Open Confirm</Button>
          <Button onClick={() => setDrawerOpen(true)} variant="secondary">Open Drawer</Button>
          <Button onClick={() => setSheetOpen(true)} variant="secondary">Open Sheet</Button>
        </Row>
        <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} title="Sample dialog" footer={<Button onClick={() => setDialogOpen(false)}>OK</Button>}>
          NM Dialog with bracket corner marks and paper-raised body.
        </Dialog>
        <ConfirmDialog
          open={confirmOpen}
          title="Reset memories?"
          message="This cannot be undone."
          confirmLabel="Reset"
          destructive
          onConfirm={() => setConfirmOpen(false)}
          onCancel={() => setConfirmOpen(false)}
        />
        <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Context">
          <p>Drawer slides in from the right by default.</p>
        </Drawer>
        <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)} title="Filter">
          <p>Mobile bottom sheet pattern.</p>
        </Sheet>
      </Section>

      <Section id="viz" label="Data Viz">
        <div className="grid grid-cols-4 gap-3 mb-4">
          <KPITile label="Active" value={4} trend={25} />
          <KPITile label="Messages" value={127} trend={14} />
          <KPITile label="Tool Calls" value={342} trend={-2} />
          <KPITile label="Cost" value="$1.24" trend={18} upIsGood={false} />
        </div>
        <StatStrip
          items={[
            { label: 'Online', value: 12 },
            { label: 'Pending', value: 3 },
            { label: 'Failed', value: 0 },
          ]}
        />
        <div className="mt-4">
          <ChartCard title="Activity · Last 24h" subtitle="Carbon = human · Silicon = AI">
            <div style={{ height: 200, background: 'var(--nm-paper-warm)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--nm-ink50)' }}>
              ECharts canvas mounts here
            </div>
          </ChartCard>
        </div>
      </Section>

      <Section id="misc" label="Misc">
        <Row>
          <Chip species="carbon">human</Chip>
          <Chip species="silicon">ai</Chip>
          <Chip species="overlap">co-write</Chip>
          <Chip onDismiss={() => {}}>dismissible</Chip>
        </Row>
        <Row>
          <Tag>BETA</Tag>
          <Tag>v1.5.7</Tag>
          <Tag>INTERNAL</Tag>
        </Row>
        <Row>
          <span>Inbox</span><Badge count={3} />
          <span>Errors</span><Badge count={150} species="ink" />
          <span>New</span><Badge count={5} dot />
        </Row>
        <CodeBlock code={'const greet = "hello world";\nconsole.log(greet);'} language="javascript" />
        <div className="mt-3">
          <Kbd keys={['Cmd', 'K']} /> &nbsp; <Kbd keys={['Shift', 'A']} separator="·" />
        </div>
        <div className="mt-3">
          <Link href="https://agent.narra.nexus" external>Open NarraNexus cloud</Link>
        </div>
      </Section>
    </div>
  );
}
